"""The compliance assessment engine.

For each ``Requirement`` object, the engine finds the controls that claim to
satisfy it (the ``satisfies`` edges from Capability/Process/Platform objects),
gathers their evidence, and *derives* an assessment status:

    SATISFIED       an approved control with fresh, traceable evidence
    PARTIAL         a control exists with evidence but it is unapproved or stale
    GAP             no control satisfies the requirement (or it has no evidence)
    NOT_APPLICABLE  set by a human; never derived

Crucially the engine never concludes compliance on its own: every assessment is
written PROPOSED and only an approved assessment counts toward coverage. A
re-run preserves prior human review decisions, exactly like ``consolidate``.

The platform invariant carries over: an assessment that asserts a requirement is
(partly) met must be backed by at least one evidence row, or it is recorded as a
GAP instead.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from ..db import connect, init_db
from . import repository as repo
from .config import ComplianceConfig, load_compliance_config
from .models import (
    EVIDENCED_STATUSES,
    AssessmentStatus,
    AssessStats,
    ComplianceReviewState,
)
from .sync import sync_requirements

LOGGER = logging.getLogger(__name__)

_STALE_FRESHNESS = {"STALE", "ARCHIVED"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _evidence_age_days(created_at: str | None, now: datetime) -> float | None:
    if not created_at:
        return None
    try:
        ts = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds() / 86400.0


def _derive_status(
    conn,
    control,
    evidence,
    config: ComplianceConfig,
    now_dt: datetime,
) -> tuple[str, str]:
    """Return ``(status, rationale)`` for one control against a requirement."""

    name = control["name"] or control["id"]
    if not evidence:
        return (
            AssessmentStatus.GAP.value,
            f"Control '{name}' satisfies this requirement but has no evidence.",
        )

    approved = (control["status"] == "APPROVED")
    if not approved:
        return (
            AssessmentStatus.PARTIAL.value,
            f"Control '{name}' has evidence but is not approved "
            f"(status {control['status']}).",
        )

    # Approved control with evidence: SATISFIED, unless the proof is stale.
    freshness = repo.control_freshness(conn, control["id"])
    if freshness in _STALE_FRESHNESS:
        return (
            AssessmentStatus.PARTIAL.value,
            f"Control '{name}' is approved but its knowledge is {freshness}; "
            "re-confirm the evidence.",
        )
    youngest = min(
        (a for a in (_evidence_age_days(e["created_at"], now_dt) for e in evidence)
         if a is not None),
        default=None,
    )
    if youngest is not None and youngest > config.stale_evidence_days:
        return (
            AssessmentStatus.PARTIAL.value,
            f"Control '{name}' is approved but its newest evidence is "
            f"{int(youngest)} days old (> {config.stale_evidence_days}).",
        )
    return (
        AssessmentStatus.SATISFIED.value,
        f"Control '{name}' is approved and backed by {len(evidence)} evidence "
        "quote(s).",
    )


def assess(
    db_path: str | Path = "data/catalog.sqlite",
    config: ComplianceConfig | None = None,
) -> AssessStats:
    """Assess every requirement and record the results. Returns aggregate stats."""

    config = config or load_compliance_config()
    init_db(db_path)
    now = _utc_now()
    now_dt = datetime.now(UTC)
    stats = AssessStats()

    with connect(db_path) as conn:
        sync_requirements(conn, now)
        prior = repo.snapshot_assessment_reviews(conn)
        repo.clear_assessments(conn)

        satisfied_requirements: set[str] = set()
        requirements = repo.requirements(conn)
        for req in requirements:
            req_id = req["object_id"]
            version = req["assessed_against_version"] or ""
            controls = repo.satisfying_controls(
                conn,
                req_id,
                control_types=config.control_types,
                only_approved=config.require_approved_controls,
            )
            stats.requirements_assessed += 1

            if not controls:
                review = prior.get((req_id, ""), ComplianceReviewState.PROPOSED.value)
                repo.insert_assessment(
                    conn,
                    requirement_object_id=req_id,
                    control_object_id=None,
                    status=AssessmentStatus.GAP.value,
                    assessed_against_version=version,
                    rationale="No control satisfies this requirement.",
                    assessor="engine",
                    review_status=review,
                    now=now,
                )
                stats.gaps += 1
                continue

            for control in controls:
                evidence = repo.control_evidence(conn, control["id"])
                status, rationale = _derive_status(
                    conn, control, evidence, config, now_dt
                )
                review = prior.get(
                    (req_id, control["id"]), ComplianceReviewState.PROPOSED.value
                )
                assessment_id = repo.insert_assessment(
                    conn,
                    requirement_object_id=req_id,
                    control_object_id=control["id"],
                    status=status,
                    assessed_against_version=version,
                    rationale=rationale,
                    assessor="engine",
                    review_status=review,
                    now=now,
                )
                if status in EVIDENCED_STATUSES:
                    for e in evidence:
                        repo.add_assessment_evidence(
                            conn,
                            assessment_id=assessment_id,
                            artifact_id=e["artifact_id"],
                            quote=e["quote"] or "",
                            clause_ref=e["clause_ref"] or "",
                            page_number=e["page_number"],
                            confidence=e["confidence"] if e["confidence"] is not None else 0.0,
                            now=now,
                        )
                if status == AssessmentStatus.SATISFIED.value:
                    stats.satisfied += 1
                    satisfied_requirements.add(req_id)
                elif status == AssessmentStatus.PARTIAL.value:
                    stats.partial += 1
                elif status == AssessmentStatus.GAP.value:
                    stats.gaps += 1

        total = len(requirements)
        stats.coverage = round(len(satisfied_requirements) / total, 4) if total else 0.0
        repo.record_run(
            conn,
            started_at=now,
            finished_at=_utc_now(),
            requirements_assessed=stats.requirements_assessed,
            satisfied=stats.satisfied,
            partial=stats.partial,
            gaps=stats.gaps,
            not_applicable=stats.not_applicable,
            coverage=stats.coverage,
            errors=stats.errors,
        )
        conn.commit()

    LOGGER.info(
        "Compliance assess complete: requirements=%d satisfied=%d partial=%d gaps=%d coverage=%.2f",
        stats.requirements_assessed, stats.satisfied, stats.partial, stats.gaps,
        stats.coverage,
    )
    return stats


def coverage(db_path: str | Path = "data/catalog.sqlite") -> dict:
    """Return per-standard coverage plus an overall figure."""

    init_db(db_path)
    with connect(db_path) as conn:
        rows = repo.coverage_by_standard(conn)
        by_standard = []
        total_all = 0
        satisfied_all = 0
        for r in rows:
            total = r["total"] or 0
            satisfied = r["satisfied"] or 0
            total_all += total
            satisfied_all += satisfied
            by_standard.append(
                {
                    "standard_object_id": r["standard_object_id"],
                    "standard_name": r["standard_name"]
                    or r["standard_object_id"]
                    or "(unattributed)",
                    "total": total,
                    "satisfied": satisfied,
                    "partial": r["partial"] or 0,
                    "coverage": round(satisfied / total, 4) if total else 0.0,
                }
            )
    overall = round(satisfied_all / total_all, 4) if total_all else 0.0
    return {"overall": overall, "standards": by_standard}


def gaps(db_path: str | Path = "data/catalog.sqlite") -> list[dict]:
    """Return requirements with no APPROVED satisfying control."""

    init_db(db_path)
    with connect(db_path) as conn:
        rows = repo.open_gaps(conn)
    return [
        {
            "object_id": r["object_id"],
            "requirement_name": r["requirement_name"] or r["object_id"],
            "clause_ref": r["clause_ref"] or "",
            "title": r["title"] or "",
            "obligation_level": r["obligation_level"] or "",
            "standard_object_id": r["standard_object_id"] or "",
            "standard_name": r["standard_name"] or r["standard_object_id"] or "",
        }
        for r in rows
    ]


def review_assessment(
    db_path: str | Path,
    assessment_id: int,
    review_status: str,
    *,
    reviewer: str = "cli",
    note: str = "",
) -> bool:
    """Approve or reject a compliance assessment, recording the audit action.

    Reuses the knowledge layer's ``knowledge_reviews`` audit trail with
    ``target_kind='compliance_assessment'`` so every sign-off is logged where the
    rest of the platform's review history lives.
    """

    if review_status not in {s.value for s in ComplianceReviewState}:
        raise ValueError(f"Unknown review status: {review_status}")
    init_db(db_path)
    now = _utc_now()
    with connect(db_path) as conn:
        changed = repo.set_assessment_review(conn, assessment_id, review_status, now)
        if changed:
            conn.execute(
                """
                INSERT INTO knowledge_reviews(
                    target_kind, target_id, action, confidence, note, reviewer, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "compliance_assessment",
                    str(assessment_id),
                    review_status,
                    None,
                    note,
                    reviewer,
                    now,
                ),
            )
            conn.commit()
    return changed


def prove(db_path: str | Path, term: str) -> dict:
    """Build a traceable compliance proof for a requirement.

    Returns the requirement, its APPROVED satisfying assessments, and the
    evidence behind them. When nothing approved and evidenced backs the
    requirement, ``proven`` is False and ``message`` carries the platform's
    standard "no evidence" decline rather than a fabricated conclusion.
    """

    init_db(db_path)
    with connect(db_path) as conn:
        req_id = repo.find_requirement_id(conn, term)
        if req_id is None:
            return {
                "found": False,
                "proven": False,
                "term": term,
                "message": f"No requirement matches '{term}'.",
                "assessments": [],
            }
        requirement = repo.get_requirement(conn, req_id)
        all_assessments = repo.assessments(conn)
        proofs = []
        for a in all_assessments:
            if a["requirement_object_id"] != req_id:
                continue
            if a["review_status"] != ComplianceReviewState.APPROVED.value:
                continue
            if a["status"] not in EVIDENCED_STATUSES:
                continue
            ev = repo.assessment_evidence(conn, a["id"])
            proofs.append(
                {
                    "assessment_id": a["id"],
                    "control_object_id": a["control_object_id"],
                    "control_name": a["control_name"],
                    "status": a["status"],
                    "rationale": a["rationale"],
                    "assessed_against_version": a["assessed_against_version"],
                    "evidence": [
                        {
                            "artifact_id": e["artifact_id"],
                            "quote": e["quote"],
                            "clause_ref": e["clause_ref"],
                            "page_number": e["page_number"],
                            "confidence": e["confidence"],
                        }
                        for e in ev
                    ],
                }
            )

    proven = bool(proofs)
    return {
        "found": True,
        "proven": proven,
        "term": term,
        "requirement": {
            "object_id": requirement["object_id"],
            "name": requirement["object_name"] or requirement["object_id"],
            "clause_ref": requirement["clause_ref"] or "",
            "title": requirement["title"] or "",
            "obligation_level": requirement["obligation_level"] or "",
        }
        if requirement
        else {"object_id": req_id},
        "assessments": proofs,
        "message": "" if proven else "No supporting evidence found.",
    }


__all__ = ["assess", "coverage", "gaps", "review_assessment", "prove"]
