"""The knowledge health dashboard.

Aggregates the governance tables into the single at-a-glance view the spec asks
for: how many objects exist, how many are approved, how many await review, how
many have gone stale, the average quality, the busiest domains, and what changed
recently. It is a pure read over the governance tables - run a scan first to make
the numbers current.
"""

from __future__ import annotations

import sqlite3

from . import domains as domain_analysis
from . import repository as repo
from .config import GovernanceConfig
from .models import FreshnessState, OPEN_REVIEW_STATES, ReviewWorkflowState


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def build_dashboard(
    conn: sqlite3.Connection, config: GovernanceConfig, *, recent_limit: int = 10
) -> dict:
    """Return the full health dashboard as a plain dict."""

    object_count = _count(conn, "SELECT COUNT(*) FROM knowledge_objects")
    approved = _count(
        conn,
        "SELECT COUNT(*) FROM knowledge_lifecycle WHERE review_state = ? AND present = 1",
        (ReviewWorkflowState.APPROVED.value,),
    )
    pending = _count(
        conn,
        "SELECT COUNT(*) FROM knowledge_lifecycle WHERE review_state IN (?, ?) AND present = 1",
        OPEN_REVIEW_STATES,
    )
    stale = _count(
        conn,
        "SELECT COUNT(*) FROM knowledge_lifecycle WHERE freshness_state IN (?, ?)",
        (FreshnessState.STALE.value, FreshnessState.ARCHIVED.value),
    )
    fresh = _count(
        conn,
        "SELECT COUNT(*) FROM knowledge_lifecycle WHERE freshness_state = ? AND present = 1",
        (FreshnessState.FRESH.value,),
    )

    recent = [
        {
            "change_type": r["change_type"],
            "object_id": r["object_id"],
            "detail": r["detail"],
            "detected_at": r["detected_at"],
        }
        for r in repo.recent_changes(conn, recent_limit)
    ]

    alert_counts = [
        {"type": r["key"], "count": r["count"]}
        for r in repo.count_open_alerts_by_type(conn)
    ]

    domains = domain_analysis.domain_health(conn)
    top_domains = [d for d in domains if d["object_count"] > 0][:5]

    return {
        "knowledge_objects": object_count,
        "approved_objects": approved,
        "pending_reviews": pending,
        "stale_objects": stale,
        "fresh_objects": fresh,
        "average_quality": repo.average_quality(conn),
        "open_alerts": _count(conn, "SELECT COUNT(*) FROM knowledge_alerts WHERE status = 'OPEN'"),
        "alerts_by_type": alert_counts,
        "top_domains": top_domains,
        "recent_changes": recent,
    }


__all__ = ["build_dashboard"]
