"""The governance scan and review-action pipeline.

``run_scan`` is the continuous-operations heartbeat. Each scan:

    1. records which objects are still *seen* (refreshing their freshness),
    2. detects change vs the previous scan (added/removed objects and
       relationships, confidence movement) and appends it to the audit trail,
    3. ages out objects that have stopped appearing,
    4. detects knowledge drift against the previous snapshot,
    5. (re)computes every object's quality score, and
    6. regenerates the alert set.

Curated state - ownership and the review workflow - is never touched by a scan;
only a reviewer changes it, through ``approve_object`` / ``archive_object`` /
``reject_object`` / ``flag_object``, each of which also writes the audit trail.

Because the governance tables reference object ids softly, all of this survives a
``consolidate`` that deletes and recreates the underlying objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..db import connect, init_db
from ..knowledge import repository as know_repo
from . import alerts as alert_engine
from . import repository as repo
from .config import GovernanceConfig, load_governance_config
from .drift import ObjectSnapshot, detect_drift
from .freshness import age_in_days, freshness_for
from .models import (
    ChangeType,
    FreshnessState,
    ReviewWorkflowState,
)
from .quality import QualityInputs, score_quality

# A quality drop of at least this many points (0-100) is a degradation.
_QUALITY_DROP_POINTS = 5.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

@dataclass
class GovernanceScanStats:
    objects_seen: int = 0
    objects_added: int = 0
    objects_removed: int = 0
    relationships_added: int = 0
    relationships_removed: int = 0
    confidence_changes: int = 0
    freshness_transitions: int = 0
    drift_findings: int = 0
    quality_degradations: int = 0
    alerts_generated: int = 0

    def as_dict(self) -> dict:
        return {
            "objects_seen": self.objects_seen,
            "objects_added": self.objects_added,
            "objects_removed": self.objects_removed,
            "relationships_added": self.relationships_added,
            "relationships_removed": self.relationships_removed,
            "confidence_changes": self.confidence_changes,
            "freshness_transitions": self.freshness_transitions,
            "drift_findings": self.drift_findings,
            "quality_degradations": self.quality_degradations,
            "alerts_generated": self.alerts_generated,
        }

@dataclass
class BulkObjectApprovalStats:
    """Counters for confidence-interval object approval."""

    objects_approved: int = 0

    def as_dict(self) -> dict:
        return {"objects_approved": self.objects_approved}


def _validate_confidence_interval(min_confidence: float, max_confidence: float) -> None:
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0.0 and 1.0")
    if not 0.0 <= max_confidence <= 1.0:
        raise ValueError("max_confidence must be between 0.0 and 1.0")
    if min_confidence > max_confidence:
        raise ValueError("min_confidence must be less than or equal to max_confidence")


def run_scan(
    db_path: str | Path = "data/catalog.sqlite",
    config: GovernanceConfig | None = None,
) -> GovernanceScanStats:
    """Run one governance scan over the current knowledge graph."""

    config = config or load_governance_config()
    init_db(db_path)
    now = _utc_now()
    stats = GovernanceScanStats()

    with connect(db_path) as conn:
        # Snapshot the previous state *before* we mutate anything.
        prev_lifecycle = repo.lifecycle_map(conn)
        prev_quality = repo.quality_map(conn)
        owned = repo.owned_object_ids(conn)
        rel_counts = repo.relationship_counts(conn)

        metrics = repo.object_metrics(conn)
        current_ids = {m["id"] for m in metrics}

        # -- 1 + 2 + 3: lifecycle refresh, change detection, ageing ----------
        for m in metrics:
            oid = m["id"]
            conf = m["confidence"] or 0.0
            prior = prev_lifecycle.get(oid)
            archived = (
                prior is not None
                and prior["review_state"] == ReviewWorkflowState.ARCHIVED.value
            )
            # A present object's evidence was seen *now*, so its age is 0.
            state, score = freshness_for(0.0, config.freshness, archived=archived)

            if prior is None:
                repo.insert_lifecycle(
                    conn,
                    object_id=oid,
                    name=m["canonical_name"],
                    object_type=m["object_type"],
                    created_at=now,
                    last_seen_at=now,
                    last_confidence=conf,
                    freshness_score=score,
                    freshness_state=state,
                    review_state=ReviewWorkflowState.PENDING_REVIEW.value,
                )
                stats.objects_added += 1
                repo.insert_change(
                    conn,
                    change_type=ChangeType.OBJECT_ADDED.value,
                    target_kind="object",
                    object_id=oid,
                    new_value=f"{m['object_type']}:{m['canonical_name']}",
                    detail=f"confidence {conf:.2f}",
                    detected_at=now,
                )
            else:
                repo.update_lifecycle_seen(
                    conn,
                    object_id=oid,
                    name=m["canonical_name"],
                    object_type=m["object_type"],
                    last_seen_at=now,
                    last_confidence=conf,
                    freshness_score=score,
                    freshness_state=state,
                    present=1,
                )
                prev_conf = prior["last_confidence"]
                if (
                    prev_conf is not None
                    and abs(conf - prev_conf) >= config.drift.min_confidence_delta
                ):
                    stats.confidence_changes += 1
                    repo.insert_change(
                        conn,
                        change_type=ChangeType.CONFIDENCE_CHANGED.value,
                        target_kind="object",
                        object_id=oid,
                        field="confidence",
                        old_value=f"{prev_conf:.3f}",
                        new_value=f"{conf:.3f}",
                        detected_at=now,
                    )
                if prior["present"] == 0:
                    # Reappeared after being absent.
                    repo.insert_change(
                        conn,
                        change_type=ChangeType.OBJECT_ADDED.value,
                        target_kind="object",
                        object_id=oid,
                        new_value=f"{m['object_type']}:{m['canonical_name']}",
                        detail="reappeared",
                        detected_at=now,
                    )

        stats.objects_seen = len(current_ids)

        # Absent objects: age them out and log removal on the first disappearance.
        for oid, prior in prev_lifecycle.items():
            if oid in current_ids:
                continue
            archived = prior["review_state"] == ReviewWorkflowState.ARCHIVED.value
            age = age_in_days(prior["last_seen_at"], now)
            state, score = freshness_for(age, config.freshness, archived=archived)
            if state != prior["freshness_state"]:
                stats.freshness_transitions += 1
                repo.insert_change(
                    conn,
                    change_type=ChangeType.FRESHNESS_CHANGED.value,
                    target_kind="object",
                    object_id=oid,
                    field="freshness_state",
                    old_value=prior["freshness_state"] or "",
                    new_value=state,
                    detail=f"{int(age)} days since last seen",
                    detected_at=now,
                )
            was_present = prior["present"] == 1
            repo.update_lifecycle_freshness(
                conn,
                object_id=oid,
                freshness_score=score,
                freshness_state=state,
                present=0,
                updated_at=now,
            )
            if was_present:
                stats.objects_removed += 1
                repo.insert_change(
                    conn,
                    change_type=ChangeType.OBJECT_REMOVED.value,
                    target_kind="object",
                    object_id=oid,
                    old_value=f"{prior['object_type']}:{prior['name']}",
                    detail="no longer in the graph",
                    detected_at=now,
                )

        # -- 2 (relationships): diff against the replayed change log ----------
        known_rels = repo.known_relationship_triples(conn)
        current_rels = repo.current_relationship_triples(conn)
        for triple in current_rels - known_rels:
            stats.relationships_added += 1
            repo.insert_change(
                conn,
                change_type=ChangeType.RELATIONSHIP_ADDED.value,
                target_kind="relationship",
                object_id=triple[0],
                new_value=f"{triple[0]} {triple[1]} {triple[2]}",
                detail="|".join(triple),
                detected_at=now,
            )
        for triple in known_rels - current_rels:
            stats.relationships_removed += 1
            repo.insert_change(
                conn,
                change_type=ChangeType.RELATIONSHIP_REMOVED.value,
                target_kind="relationship",
                object_id=triple[0],
                old_value=f"{triple[0]} {triple[1]} {triple[2]}",
                detail="|".join(triple),
                detected_at=now,
            )

        # -- 4: drift detection against the previous snapshot ----------------
        prev_snapshot = {
            oid: ObjectSnapshot(
                object_id=oid,
                name=prior["name"] or oid,
                object_type=prior["object_type"] or "",
                document_count=(
                    prev_quality[oid]["document_count"]
                    if oid in prev_quality and prev_quality[oid]["document_count"] is not None
                    else 0
                ),
            )
            for oid, prior in prev_lifecycle.items()
            if prior["present"] == 1
        }
        cur_snapshot = {
            m["id"]: ObjectSnapshot(
                object_id=m["id"],
                name=m["canonical_name"],
                object_type=m["object_type"],
                document_count=m["document_count"] or 0,
            )
            for m in metrics
        }
        drift_findings = detect_drift(prev_snapshot, cur_snapshot, config.drift)
        stats.drift_findings = len(drift_findings)
        for finding in drift_findings:
            repo.insert_change(
                conn,
                change_type=ChangeType.DRIFT_DETECTED.value,
                target_kind="object",
                object_id=finding.object_id,
                field=finding.kind,
                new_value=finding.related_id,
                detail=finding.message,
                detected_at=now,
            )

        # -- 5: quality scoring ----------------------------------------------
        degradations: list[dict] = []
        for m in metrics:
            oid = m["id"]
            life = repo.get_lifecycle(conn, oid)
            review_state = (
                life["review_state"] if life else ReviewWorkflowState.PENDING_REVIEW.value
            )
            fresh_score = life["freshness_score"] if life else 1.0
            total, rejected = rel_counts.get(oid, (0, 0))
            result = score_quality(
                QualityInputs(
                    evidence_count=m["evidence_count"] or 0,
                    review_state=review_state,
                    freshness_score=fresh_score if fresh_score is not None else 1.0,
                    relationship_total=total,
                    relationship_rejected=rejected,
                    has_owner=oid in owned,
                    confidence=m["confidence"] or 0.0,
                ),
                config.quality,
            )
            repo.upsert_quality(
                conn,
                {
                    "object_id": oid,
                    "evidence_count": m["evidence_count"] or 0,
                    "document_count": m["document_count"] or 0,
                    "computed_at": now,
                    **result,
                },
            )
            prev = prev_quality.get(oid)
            if (
                prev is not None
                and prev["quality_score"] is not None
                and result["quality_score"] <= prev["quality_score"] - _QUALITY_DROP_POINTS
            ):
                degradations.append(
                    {
                        "object_id": oid,
                        "previous": prev["quality_score"],
                        "current": result["quality_score"],
                    }
                )
        stats.quality_degradations = len(degradations)
        repo.remove_quality_for_absent(conn)

        # -- 6: alerts -------------------------------------------------------
        stats.alerts_generated = alert_engine.generate_alerts(
            conn,
            config,
            now,
            quality_degradations=degradations,
            drift_findings=drift_findings,
        )

        conn.commit()

    return stats


# -- review actions -----------------------------------------------------------

# Workflow states that also pin the consolidation/export status of the object,
# so an approval flows into the RDF projection and a rejection out of it.
_OBJECT_STATUS_FOR = {
    ReviewWorkflowState.APPROVED.value: "APPROVED",
    ReviewWorkflowState.REJECTED.value: "REJECTED",
    ReviewWorkflowState.ARCHIVED.value: "ARCHIVED",
}


def _apply_review(
    db_path: str | Path,
    object_id: str,
    workflow_state: str,
    *,
    reviewer: str,
    note: str,
    confirmed: bool,
    archive_freshness: bool = False,
    force_export_status: str | None = None,
) -> bool:
    init_db(db_path)
    now = _utc_now()
    with connect(db_path) as conn:
        obj = know_repo.get_object(conn, object_id)
        if obj is None:
            return False
        prior_life = repo.get_lifecycle(conn, object_id)
        prior_state = prior_life["review_state"] if prior_life else ""

        repo.set_review_state(
            conn,
            object_id=object_id,
            review_state=workflow_state,
            reviewed_at=now,
            confirmed=confirmed,
        )
        if archive_freshness:
            repo.update_lifecycle_freshness(
                conn,
                object_id=object_id,
                freshness_score=0.0,
                freshness_state=FreshnessState.ARCHIVED.value,
                present=1 if prior_life is None else prior_life["present"],
                updated_at=now,
            )

        # Normal review actions only pin the export status for the three states
        # that map to it; revert passes ``force_export_status`` so it can also
        # reset an object back to PROPOSED (un-exporting it) when undoing an
        # approval back to an open review state.
        new_status = (
            force_export_status
            if force_export_status is not None
            else _OBJECT_STATUS_FOR.get(workflow_state)
        )
        if new_status is not None:
            know_repo.set_object_status(conn, object_id, new_status, now)

        know_repo.record_review(
            conn,
            target_kind="object",
            target_id=object_id,
            action=workflow_state,
            confidence=None,
            note=note,
            reviewer=reviewer,
            created_at=now,
        )
        repo.insert_change(
            conn,
            change_type=ChangeType.REVIEW_CHANGED.value,
            target_kind="object",
            object_id=object_id,
            field="review_state",
            old_value=prior_state,
            new_value=workflow_state,
            detail=f"by {reviewer}" + (f": {note}" if note else ""),
            detected_at=now,
        )
        conn.commit()
    return True


def approve_object(
    db_path: str | Path, object_id: str, *, reviewer: str = "cli", note: str = ""
) -> bool:
    """Approve an object: it becomes trusted and flows into the RDF projection."""

    return _apply_review(
        db_path,
        object_id,
        ReviewWorkflowState.APPROVED.value,
        reviewer=reviewer,
        note=note,
        confirmed=True,
    )


def approve_objects_by_confidence(
    db_path: str | Path,
    min_confidence: float,
    max_confidence: float,
    *,
    reviewer: str = "cli",
    note: str = "",
    current_status: str = "PROPOSED",
) -> BulkObjectApprovalStats:
    """Approve objects with confidence inside an inclusive interval."""

    _validate_confidence_interval(min_confidence, max_confidence)
    init_db(db_path)
    stats = BulkObjectApprovalStats()
    with connect(db_path) as conn:
        rows = know_repo.objects_in_confidence_interval(
            conn, min_confidence, max_confidence, status=current_status
        )
    for row in rows:
        if approve_object(db_path, row["id"], reviewer=reviewer, note=note):
            stats.objects_approved += 1
    return stats


def archive_object(
    db_path: str | Path, object_id: str, *, reviewer: str = "cli", note: str = ""
) -> bool:
    """Archive an object: retire it from the trusted graph but keep its history."""

    return _apply_review(
        db_path,
        object_id,
        ReviewWorkflowState.ARCHIVED.value,
        reviewer=reviewer,
        note=note,
        confirmed=False,
        archive_freshness=True,
    )


def reject_object(
    db_path: str | Path, object_id: str, *, reviewer: str = "cli", note: str = ""
) -> bool:
    """Reject an object: it is not trusted and is excluded from the projection."""

    return _apply_review(
        db_path,
        object_id,
        ReviewWorkflowState.REJECTED.value,
        reviewer=reviewer,
        note=note,
        confirmed=False,
    )


def flag_object(
    db_path: str | Path, object_id: str, *, reviewer: str = "cli", note: str = ""
) -> bool:
    """Flag an object as needing attention without otherwise changing its status."""

    return _apply_review(
        db_path,
        object_id,
        ReviewWorkflowState.NEEDS_ATTENTION.value,
        reviewer=reviewer,
        note=note,
        confirmed=False,
    )


# Open review states are not exported, so reverting an object *to* one must reset
# its consolidation status back to PROPOSED (the other three states map 1:1).
_REVERT_OPEN_STATES = {
    ReviewWorkflowState.PENDING_REVIEW.value,
    ReviewWorkflowState.NEEDS_ATTENTION.value,
}


def apply_object_state(
    db_path: str | Path,
    object_id: str,
    workflow_state: str,
    *,
    reviewer: str = "cli",
    note: str = "",
) -> bool:
    """Set an object's review state to an explicit value, syncing export status.

    Unlike :func:`approve_object` and friends (each of which targets one state),
    this applies *any* workflow state and keeps the export status consistent —
    including resetting to PROPOSED when the target is an open review state. It is
    the building block the revert path uses to truly undo an approval.
    """

    if workflow_state not in {s.value for s in ReviewWorkflowState}:
        raise ValueError(f"Unknown review workflow state: {workflow_state}")
    export_status = (
        "PROPOSED"
        if workflow_state in _REVERT_OPEN_STATES
        else _OBJECT_STATUS_FOR[workflow_state]
    )
    return _apply_review(
        db_path,
        object_id,
        workflow_state,
        reviewer=reviewer,
        note=note,
        confirmed=workflow_state == ReviewWorkflowState.APPROVED.value,
        archive_freshness=workflow_state == ReviewWorkflowState.ARCHIVED.value,
        force_export_status=export_status,
    )


__all__ = [
    "GovernanceScanStats",
    "run_scan",
    "approve_object",
    "approve_objects_by_confidence",
    "archive_object",
    "reject_object",
    "flag_object",
    "apply_object_state",
]
