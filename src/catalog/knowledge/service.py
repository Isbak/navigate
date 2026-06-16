"""The knowledge consolidation pipeline.

Transforms the document-level proposals of the semantic layer into reusable,
cross-document knowledge objects, in six phases:

    Phase 1  Gather all entities          (read the candidate_* tables)
    Phase 2  Group similar entities       (entity resolution -> clusters)
    Phase 3  Suggest merges               (fuzzy + optional LLM, with confidence)
    Phase 4  Create canonical objects     (stable URI-ready ids, scored)
    Phase 5  Attach evidence              (every object is traceable)
    Phase 6  Create relationships         (typed links between objects)

A normal ``consolidate`` rebuilds the derived tables from scratch but, because
ids are stable, re-applies any prior human review decisions to the same object.
``consolidate(force=True)`` discards the review history too. An optional LLM
provider only ever influences borderline merge decisions; everything else is
deterministic and offline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..db import connect, init_db
from . import repository as repo
from .ids import equation_display_name, object_id, requirement_display_name
from .models import Cluster, ReviewState
from .resolution import (
    ResolutionConfig,
    cluster_mentions,
    normalize_name,
    similarity,
)
from .scoring import ScoringConfig, ScoringInputs, score_object

LOGGER = logging.getLogger(__name__)

_DESCRIPTION_MAX = 500
_EVIDENCE_QUOTES_PER_RELATIONSHIP = 3
# Human decisions worth carrying across a non-force rebuild.
_PRESERVED_STATUSES = {
    ReviewState.REVIEWED.value,
    ReviewState.APPROVED.value,
    ReviewState.REJECTED.value,
    ReviewState.ARCHIVED.value,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass
class BulkApprovalStats:
    """Counters for approving knowledge graph rows by confidence interval."""

    objects_approved: int = 0
    relationships_approved: int = 0

    def as_dict(self) -> dict:
        return {
            "objects_approved": self.objects_approved,
            "relationships_approved": self.relationships_approved,
        }


def _validate_confidence_interval(min_confidence: float, max_confidence: float) -> None:
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0.0 and 1.0")
    if not 0.0 <= max_confidence <= 1.0:
        raise ValueError("max_confidence must be between 0.0 and 1.0")
    if min_confidence > max_confidence:
        raise ValueError("min_confidence must be less than or equal to max_confidence")

@dataclass
class ConsolidationStats:
    """Aggregate counters for one ``consolidate`` run."""

    mentions_gathered: int = 0
    objects_created: int = 0
    mentions_linked: int = 0
    evidence_created: int = 0
    relationships_created: int = 0
    relationships_unresolved: int = 0
    statuses_preserved: int = 0
    by_object_type: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "mentions_gathered": self.mentions_gathered,
            "objects_created": self.objects_created,
            "mentions_linked": self.mentions_linked,
            "evidence_created": self.evidence_created,
            "relationships_created": self.relationships_created,
            "relationships_unresolved": self.relationships_unresolved,
            "statuses_preserved": self.statuses_preserved,
        }


def _assign_ids(clusters: list[Cluster]) -> list[tuple[str, Cluster]]:
    """Give each cluster a stable, collision-free URI-ready id."""

    assigned: list[tuple[str, Cluster]] = []
    used: set[str] = set()
    for cluster in clusters:
        base = object_id(cluster.object_type, cluster.canonical_name)
        oid = base
        suffix = 2
        while oid in used:
            oid = f"{base}_{suffix}"
            suffix += 1
        used.add(oid)
        assigned.append((oid, cluster))
    return assigned


def _build_resolver(
    assigned: list[tuple[str, Cluster]],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Index every member surface form to its object id.

    Returns ``(exact_index, canonical_list)`` where ``exact_index`` maps a
    normalized name to an object id and ``canonical_list`` is ``[(canonical_name,
    object_id)]`` for fuzzy fallback when an exact key misses.
    """

    exact: dict[str, str] = {}
    canonical: list[tuple[str, str]] = []
    for oid, cluster in assigned:
        canonical.append((cluster.canonical_name, oid))
        for mention in cluster.mentions:
            exact.setdefault(normalize_name(mention.name), oid)
        exact.setdefault(normalize_name(cluster.canonical_name), oid)
    return exact, canonical


def _resolve_endpoint(
    text: str,
    exact: dict[str, str],
    canonical: list[tuple[str, str]],
    config: ResolutionConfig,
) -> str | None:
    """Map a free-text relationship endpoint to a known object id, or None."""

    oid = exact.get(normalize_name(text))
    if oid is not None:
        return oid
    best_id, best_score = None, 0.0
    for name, candidate_id in canonical:
        score = similarity(text, name)
        if score > best_score:
            best_id, best_score = candidate_id, score
    return best_id if best_score >= config.auto_merge_threshold else None

@dataclass
class _ResolvedRel:
    confidence: float
    quotes: list[dict]


def _resolve_relationships(
    candidate_rows,
    exact: dict[str, str],
    canonical: list[tuple[str, str]],
    config: ResolutionConfig,
    stats: ConsolidationStats,
) -> dict[tuple[str, str, str], _ResolvedRel]:
    """Aggregate per-document relationships into object-level ones."""

    resolved: dict[tuple[str, str, str], _ResolvedRel] = {}
    for row in candidate_rows:
        src = _resolve_endpoint(row["subject"], exact, canonical, config)
        tgt = _resolve_endpoint(row["object"], exact, canonical, config)
        if src is None or tgt is None or src == tgt:
            stats.relationships_unresolved += 1
            continue
        key = (src, row["predicate"], tgt)
        conf = row["confidence"] if row["confidence"] is not None else 0.0
        entry = resolved.get(key)
        if entry is None:
            entry = _ResolvedRel(confidence=conf, quotes=[])
            resolved[key] = entry
        entry.confidence = max(entry.confidence, conf)
        quote = (row["supporting_text"] or "").strip()
        if quote and len(entry.quotes) < _EVIDENCE_QUOTES_PER_RELATIONSHIP:
            entry.quotes.append({"artifact_id": row["artifact_id"], "quote": quote})
    return resolved


def _add_mandated_by_relationships(
    conn,
    resolved: dict[tuple[str, str, str], _ResolvedRel],
    exact: dict[str, str],
    canonical: list[tuple[str, str]],
    config: ResolutionConfig,
) -> None:
    """Link each Requirement object to the Standard that mandates it.

    The requirement->standard edge is not a free-text candidate relationship; it
    is implied by the ``candidate_requirements`` rows. Both endpoints resolve
    through the same name index the other relationships use, so a ``mandated_by``
    edge is only added when both objects actually exist.
    """

    rows = repo.gather_candidate_requirements(conn, config.min_mention_confidence)
    for row in rows:
        standard_name = (row["standard_name"] or "").strip()
        if not standard_name:
            continue
        req_name = requirement_display_name(
            row["standard_name"] or "", row["clause_ref"] or "", row["title"] or ""
        )
        src = _resolve_endpoint(req_name, exact, canonical, config)
        tgt = _resolve_endpoint(standard_name, exact, canonical, config)
        if src is None or tgt is None or src == tgt:
            continue
        key = (src, "mandated_by", tgt)
        conf = row["confidence"] if row["confidence"] is not None else 0.0
        entry = resolved.get(key)
        if entry is None:
            entry = _ResolvedRel(confidence=conf, quotes=[])
            resolved[key] = entry
        entry.confidence = max(entry.confidence, conf)
        quote = (row["supporting_text"] or row["requirement_text"] or "").strip()
        if quote and len(entry.quotes) < _EVIDENCE_QUOTES_PER_RELATIONSHIP:
            entry.quotes.append({"artifact_id": row["artifact_id"], "quote": quote})


def _add_equation_relationships(
    conn,
    resolved: dict[tuple[str, str, str], _ResolvedRel],
    exact: dict[str, str],
    canonical: list[tuple[str, str]],
    config: ResolutionConfig,
) -> None:
    """Link each Equation object to its Standard and the Requirement it specifies.

    Like ``mandated_by``, these edges are implied by the ``candidate_equations``
    rows rather than mined as free-text relationships: an equation is
    ``mandated_by`` its standard, and a requirement on the same clause
    ``specifies`` the equation. Both endpoints resolve through the shared name
    index, so an edge is only added when both objects actually exist.
    """

    def _record(src: str, predicate: str, tgt: str, row) -> None:
        if src is None or tgt is None or src == tgt:
            return
        key = (src, predicate, tgt)
        conf = row["confidence"] if row["confidence"] is not None else 0.0
        entry = resolved.get(key)
        if entry is None:
            entry = _ResolvedRel(confidence=conf, quotes=[])
            resolved[key] = entry
        entry.confidence = max(entry.confidence, conf)
        quote = (row["supporting_text"] or row["expression"] or "").strip()
        if quote and len(entry.quotes) < _EVIDENCE_QUOTES_PER_RELATIONSHIP:
            entry.quotes.append({"artifact_id": row["artifact_id"], "quote": quote})

    rows = repo.gather_candidate_equations(conn, config.min_mention_confidence)
    for row in rows:
        standard_name = (row["standard_name"] or "").strip()
        eq_name = equation_display_name(
            standard_name, row["symbol"] or "", row["clause_ref"] or ""
        )
        eq_id = _resolve_endpoint(eq_name, exact, canonical, config)
        if eq_id is None:
            continue
        if standard_name:
            std_id = _resolve_endpoint(standard_name, exact, canonical, config)
            _record(eq_id, "mandated_by", std_id, row)
            clause = (row["clause_ref"] or "").strip()
            if clause:
                req_name = requirement_display_name(standard_name, clause, "")
                req_id = _resolve_endpoint(req_name, exact, canonical, config)
                _record(req_id, "specifies", eq_id, row)


def _object_description(cluster: Cluster) -> str:
    """Use the most informative supporting quote as a representative description."""

    quotes = [m.source_text.strip() for m in cluster.mentions if m.source_text.strip()]
    if not quotes:
        return ""
    return max(quotes, key=len)[:_DESCRIPTION_MAX]


def _persist_object(
    conn,
    *,
    oid: str,
    cluster: Cluster,
    confidence: float,
    status: str,
    now: str,
    stats: ConsolidationStats,
) -> None:
    """Phase 4 + 5: write the object, its mentions, and its evidence.

    Enforces the invariant that no knowledge object exists without evidence: if
    no mention carried a supporting quote, a single fallback evidence row is
    written from the strongest mention so the object is still traceable.
    """

    repo.insert_object(
        conn,
        id=oid,
        name=cluster.canonical_name,
        object_type=cluster.object_type,
        description=_object_description(cluster),
        canonical_name=cluster.canonical_name,
        confidence=confidence,
        status=status,
        merge_confidence=cluster.merge_confidence,
        created_at=now,
    )

    evidence_written = 0
    for mention in cluster.mentions:
        repo.insert_mention(
            conn,
            knowledge_object_id=oid,
            artifact_id=mention.artifact_id,
            confidence=mention.confidence,
            source_text=mention.source_text,
            created_at=now,
        )
        stats.mentions_linked += 1
        quote = mention.source_text.strip()
        if quote:
            repo.insert_evidence(
                conn,
                knowledge_object_id=oid,
                artifact_id=mention.artifact_id,
                quote=quote,
                page_number=None,
                slide_number=None,
                confidence=mention.confidence,
                created_at=now,
            )
            evidence_written += 1

    if evidence_written == 0:
        # Fallback so the "no object without evidence" invariant always holds.
        strongest = max(cluster.mentions, key=lambda m: m.confidence)
        repo.insert_evidence(
            conn,
            knowledge_object_id=oid,
            artifact_id=strongest.artifact_id,
            quote=cluster.canonical_name,
            page_number=None,
            slide_number=None,
            confidence=strongest.confidence,
            created_at=now,
        )
        evidence_written = 1

    stats.evidence_created += evidence_written
    stats.objects_created += 1
    stats.by_object_type[cluster.object_type] = (
        stats.by_object_type.get(cluster.object_type, 0) + 1
    )


def consolidate(
    db_path: str | Path = "data/catalog.sqlite",
    *,
    force: bool = False,
    config: ResolutionConfig | None = None,
    scoring: ScoringConfig | None = None,
    merge_judge: Callable[[str, str, str], bool] | None = None,
) -> ConsolidationStats:
    """Run the full consolidation pipeline and return aggregate stats.

    ``merge_judge`` is the optional LLM-assisted merge hook. ``force`` discards
    prior human review decisions; otherwise they are preserved by stable id.
    """

    config = config or ResolutionConfig()
    scoring = scoring or ScoringConfig()
    init_db(db_path)
    now = _utc_now()
    stats = ConsolidationStats()

    with connect(db_path) as conn:
        # Snapshot human decisions before we rebuild (unless forced).
        prior_object_status: dict[str, str] = {}
        prior_rel_status: dict[tuple[str, str, str], str] = {}
        if not force:
            prior_object_status = repo.snapshot_object_statuses(conn)
            prior_rel_status = repo.snapshot_relationship_statuses(conn)
            repo.clear_consolidated(conn)
        else:
            repo.clear_all(conn)

        # Phase 1: gather.
        mentions = repo.gather_mentions(conn, config.min_mention_confidence)
        stats.mentions_gathered = len(mentions)

        # Phases 2 + 3: group + suggest merges.
        clusters = cluster_mentions(mentions, config, merge_judge=merge_judge)
        assigned = _assign_ids(clusters)
        exact, canonical = _build_resolver(assigned)

        # Phase 6 (resolved up front so scoring can see relationship consistency).
        candidate_rows = repo.gather_candidate_relationships(
            conn, config.min_mention_confidence
        )
        resolved_rels = _resolve_relationships(
            candidate_rows, exact, canonical, config, stats
        )
        # Compliance: add the implied Requirement -> Standard ``mandated_by`` edges.
        _add_mandated_by_relationships(conn, resolved_rels, exact, canonical, config)
        # Compliance: add the implied Equation edges (mandated_by / specifies).
        _add_equation_relationships(conn, resolved_rels, exact, canonical, config)

        rel_total: dict[str, int] = {}
        rel_rejected: dict[str, int] = {}
        for src, pred, tgt in resolved_rels:
            status = prior_rel_status.get((src, pred, tgt), ReviewState.PROPOSED.value)
            for endpoint in (src, tgt):
                rel_total[endpoint] = rel_total.get(endpoint, 0) + 1
                if status == ReviewState.REJECTED.value:
                    rel_rejected[endpoint] = rel_rejected.get(endpoint, 0) + 1

        # Phases 4 + 5: create scored objects with evidence.
        for oid, cluster in assigned:
            prior_status = prior_object_status.get(oid)
            status = (
                prior_status
                if prior_status in _PRESERVED_STATUSES
                else ReviewState.PROPOSED.value
            )
            if prior_status in _PRESERVED_STATUSES:
                stats.statuses_preserved += 1

            review_actions = repo.review_actions_for(conn, oid)
            confidence = score_object(
                ScoringInputs(
                    mention_confidences=[m.confidence for m in cluster.mentions],
                    document_count=len(cluster.artifact_ids),
                    mention_count=len(cluster.mentions),
                    relationship_total=rel_total.get(oid, 0),
                    relationship_rejected=rel_rejected.get(oid, 0),
                    review_actions=review_actions,
                ),
                scoring,
            )
            _persist_object(
                conn,
                oid=oid,
                cluster=cluster,
                confidence=confidence,
                status=status,
                now=now,
                stats=stats,
            )

        # Persist the resolved relationships.
        for (src, pred, tgt), entry in resolved_rels.items():
            status = prior_rel_status.get((src, pred, tgt), ReviewState.PROPOSED.value)
            repo.upsert_relationship(
                conn,
                source_object=src,
                predicate=pred,
                target_object=tgt,
                confidence=round(entry.confidence, 3),
                evidence=json.dumps(entry.quotes),
                review_status=status,
                created_at=now,
            )
            stats.relationships_created += 1

        # Enrich the compliance metadata tables (clause refs, versions, standard
        # links) for any Standard/Requirement objects just (re)built. Imported
        # lazily: the compliance layer depends on this module's id helpers, so a
        # top-level import would be a cycle. The compliance tables are curated and
        # survive a consolidate; this only refreshes them from current candidates.
        from ..compliance.sync import sync_equations, sync_requirements

        sync_requirements(conn, now)
        sync_equations(conn, now)

        conn.commit()

    LOGGER.info(
        "Consolidation complete: objects=%d mentions=%d evidence=%d relationships=%d",
        stats.objects_created,
        stats.mentions_linked,
        stats.evidence_created,
        stats.relationships_created,
    )
    return stats


def review_object(
    db_path: str | Path,
    object_id: str,
    status: str,
    *,
    note: str = "",
    reviewer: str = "cli",
) -> bool:
    """Set an object's review status and record the action in the audit trail."""

    init_db(db_path)
    if status not in {s.value for s in ReviewState}:
        raise ValueError(f"Unknown review status: {status}")
    now = _utc_now()
    with connect(db_path) as conn:
        changed = repo.set_object_status(conn, object_id, status, now)
        if changed:
            repo.record_review(
                conn,
                target_kind="object",
                target_id=object_id,
                action=status,
                confidence=None,
                note=note,
                reviewer=reviewer,
                created_at=now,
            )
            conn.commit()
    return changed


def review_relationship(
    db_path: str | Path,
    relationship_id: int,
    status: str,
    *,
    note: str = "",
    reviewer: str = "cli",
) -> bool:
    """Set a relationship's review status and record it in the audit trail.

    Mirrors :func:`review_object` for the typed links between objects. A normal
    ``consolidate`` preserves the resulting status (it is keyed on the
    ``(source, predicate, target)`` triple, not the row id), so an approval
    survives re-consolidation; ``consolidate(force=True)`` discards it.
    """

    init_db(db_path)
    if status not in {s.value for s in ReviewState}:
        raise ValueError(f"Unknown review status: {status}")
    now = _utc_now()
    with connect(db_path) as conn:
        changed = repo.set_relationship_status(conn, relationship_id, status, now)
        if changed:
            repo.record_review(
                conn,
                target_kind="relationship",
                target_id=str(relationship_id),
                action=status,
                confidence=None,
                note=note,
                reviewer=reviewer,
                created_at=now,
            )
            conn.commit()
    return changed


def approve_relationships_by_confidence(
    db_path: str | Path,
    min_confidence: float,
    max_confidence: float,
    *,
    reviewer: str = "cli",
    note: str = "",
    current_status: str = ReviewState.PROPOSED.value,
) -> BulkApprovalStats:
    """Approve relationships with confidence inside an inclusive interval."""

    _validate_confidence_interval(min_confidence, max_confidence)
    init_db(db_path)
    stats = BulkApprovalStats()
    now = _utc_now()
    with connect(db_path) as conn:
        rows = repo.relationships_in_confidence_interval(
            conn, min_confidence, max_confidence, review_status=current_status
        )
        for row in rows:
            if repo.set_relationship_status(
                conn, row["id"], ReviewState.APPROVED.value, now
            ):
                repo.record_review(
                    conn,
                    target_kind="relationship",
                    target_id=str(row["id"]),
                    action=ReviewState.APPROVED.value,
                    confidence=row["confidence"],
                    note=note,
                    reviewer=reviewer,
                    created_at=now,
                )
                stats.relationships_approved += 1
        conn.commit()
    return stats


__all__ = [
    "BulkApprovalStats",
    "ConsolidationStats",
    "consolidate",
    "review_object",
    "review_relationship",
    "approve_relationships_by_confidence",
]
