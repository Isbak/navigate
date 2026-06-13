"""Alert generation.

Alerts are the operator-facing output of a governance scan: the conditions a
steward should act on. They are fully derived from the current state of the
graph plus the freshly computed lifecycle/quality, so every scan clears the open
alerts and regenerates them - there is no stale alert backlog to manage.

The conditions are exactly those the spec names: stale knowledge, stale reviews,
orphaned objects, missing owners, conflicting evidence, duplicate objects,
duplicate relationships, quality degradation, and knowledge drift. Drift and
degradation findings are computed by the service (they need the previous
snapshot) and handed in; everything else is read here from the database.
"""

from __future__ import annotations

import sqlite3

from ..knowledge import analytics as know_analytics
from . import orphans
from . import repository as repo
from .config import GovernanceConfig
from .freshness import age_in_days
from .models import AlertSeverity, AlertType, FreshnessState, ReviewWorkflowState


def _duplicate_relationship_pairs(conn: sqlite3.Connection) -> list[dict]:
    """Object pairs joined by more than one non-rejected relationship.

    The schema's unique index already forbids an identical (source, predicate,
    target) triple, so a repeated *unordered pair* means two different predicates
    (or a predicate plus its inverse) connect the same two objects - a likely
    redundancy worth a reviewer's glance.
    """

    rows = conn.execute(
        """
        SELECT source_object, predicate, target_object
        FROM knowledge_relationships
        WHERE review_status != 'REJECTED'
        """
    ).fetchall()
    seen: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        pair = tuple(sorted((r["source_object"], r["target_object"])))
        seen.setdefault(pair, []).append(r["predicate"])
    return [
        {"left": pair[0], "right": pair[1], "predicates": preds}
        for pair, preds in seen.items()
        if len(preds) > 1
    ]


def generate_alerts(
    conn: sqlite3.Connection,
    config: GovernanceConfig,
    now: str,
    *,
    quality_degradations: list[dict] | None = None,
    drift_findings: list | None = None,
) -> int:
    """Clear open alerts and regenerate them from current state. Returns the count."""

    repo.clear_open_alerts(conn)
    count = 0

    def emit(alert_type: str, severity: str, object_id: str | None, message: str) -> None:
        nonlocal count
        repo.insert_alert(
            conn,
            alert_type=alert_type,
            severity=severity,
            object_id=object_id,
            message=message,
            created_at=now,
        )
        count += 1

    # Stale knowledge (STALE or ARCHIVED freshness).
    for row in repo.lifecycle_by_freshness(
        conn, (FreshnessState.STALE.value, FreshnessState.ARCHIVED.value)
    ):
        emit(
            AlertType.STALE_KNOWLEDGE.value,
            AlertSeverity.WARNING.value,
            row["object_id"],
            f"{row['object_id']} is {row['freshness_state']} "
            f"(freshness {row['freshness_score']:.2f})",
        )

    # Stale reviews: approved long ago (or never confirmed) past the threshold.
    threshold = config.review.stale_review_days
    for row in conn.execute("SELECT * FROM knowledge_lifecycle WHERE present = 1").fetchall():
        if row["review_state"] != ReviewWorkflowState.APPROVED.value:
            continue
        anchor = row["last_reviewed_at"] or row["created_at"]
        days = age_in_days(anchor, now)
        if days >= threshold:
            emit(
                AlertType.STALE_REVIEW.value,
                AlertSeverity.WARNING.value,
                row["object_id"],
                f"{row['object_id']} has had no review in {int(days)} days",
            )

    # Orphaned objects (no relationships or no evidence).
    orphan_report = orphans.all_orphans(conn)
    for item in orphan_report["objects_without_relationships"]:
        emit(
            AlertType.ORPHANED_OBJECT.value,
            AlertSeverity.INFO.value,
            item["id"],
            f"{item['name']} has no relationships",
        )
    for item in orphan_report["objects_without_evidence"]:
        emit(
            AlertType.ORPHANED_OBJECT.value,
            AlertSeverity.CRITICAL.value,
            item["id"],
            f"{item['name']} has no supporting evidence",
        )

    # Missing owners.
    for item in orphan_report["objects_without_owner"]:
        emit(
            AlertType.MISSING_OWNER.value,
            AlertSeverity.INFO.value,
            item["id"],
            f"{item['name']} has no owner assigned",
        )

    # Conflicting evidence.
    for item in know_analytics.conflicting_evidence(conn, limit=50):
        emit(
            AlertType.CONFLICTING_EVIDENCE.value,
            AlertSeverity.WARNING.value,
            item["id"],
            f"{item['name']} has conflicting evidence "
            f"(confidence {item['min_confidence']:.2f}-{item['max_confidence']:.2f})",
        )

    # Duplicate objects.
    for dup in know_analytics.duplicate_candidates(conn, limit=50):
        emit(
            AlertType.DUPLICATE_OBJECT.value,
            AlertSeverity.INFO.value,
            dup["left_id"],
            f"Possible duplicate: {dup['left_name']} <-> {dup['right_name']} "
            f"({dup['similarity']:.2f})",
        )

    # Duplicate relationships.
    for pair in _duplicate_relationship_pairs(conn):
        emit(
            AlertType.DUPLICATE_RELATIONSHIP.value,
            AlertSeverity.INFO.value,
            pair["left"],
            f"{pair['left']} and {pair['right']} are linked by multiple predicates: "
            f"{', '.join(pair['predicates'])}",
        )

    # Quality degradation (computed by the service against the previous scan).
    for deg in quality_degradations or []:
        emit(
            AlertType.QUALITY_DEGRADATION.value,
            AlertSeverity.WARNING.value,
            deg["object_id"],
            f"{deg['object_id']} quality fell from {deg['previous']:.1f} to {deg['current']:.1f}",
        )

    # Knowledge drift (computed by the service against the previous snapshot).
    for finding in drift_findings or []:
        emit(
            AlertType.KNOWLEDGE_DRIFT.value,
            AlertSeverity.WARNING.value,
            finding.object_id,
            finding.message,
        )

    return count


__all__ = ["generate_alerts"]
