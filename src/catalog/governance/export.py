"""Governance JSON exports.

Writes the four reports the spec names so governance results can feed dashboards,
audits, or downstream tooling:

* ``quality_report.json``    - every object's quality score and factors
* ``governance_report.json`` - ownership, lifecycle, review state, and orphans
* ``knowledge_health.json``  - the dashboard summary plus domain health
* ``change_log.json``        - the full audit trail

All four are derived from the governance tables, so they reflect the most recent
``catalog governance scan``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import dashboard as dashboard_mod
from . import domains as domain_analysis
from . import orphans
from . import repository as repo
from .config import GovernanceConfig

DEFAULT_OUT_DIR = "exports/governance"


def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor]


def quality_report(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in repo.quality_ranked(conn, ascending=False)]


def governance_report(conn: sqlite3.Connection, config: GovernanceConfig) -> dict:
    objects = conn.execute(
        """
        SELECT o.id, o.canonical_name, o.object_type, o.confidence,
               l.review_state, l.freshness_state, l.freshness_score,
               l.created_at, l.last_seen_at, l.last_reviewed_at,
               w.owner_type, w.owner_id,
               q.quality_score
        FROM knowledge_objects o
        LEFT JOIN knowledge_lifecycle l ON l.object_id = o.id
        LEFT JOIN knowledge_owners w ON w.object_id = o.id
        LEFT JOIN knowledge_quality q ON q.object_id = o.id
        ORDER BY o.id
        """
    ).fetchall()
    return {
        "objects": [dict(r) for r in objects],
        "orphans": orphans.all_orphans(conn),
        "domains": domain_analysis.domain_health(conn, config),
    }


def change_log_report(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in repo.all_changes(conn)]


def export_governance(
    conn: sqlite3.Connection,
    config: GovernanceConfig,
    out_dir: str | Path = DEFAULT_OUT_DIR,
) -> dict[str, Path]:
    """Write all four governance reports to ``out_dir`` and return their paths."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    reports = {
        "quality_report.json": quality_report(conn),
        "governance_report.json": governance_report(conn, config),
        "knowledge_health.json": dashboard_mod.build_dashboard(conn, config),
        "change_log.json": change_log_report(conn),
    }

    paths: dict[str, Path] = {}
    for filename, payload in reports.items():
        path = out / filename
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[filename] = path
    return paths


__all__ = [
    "DEFAULT_OUT_DIR",
    "quality_report",
    "governance_report",
    "change_log_report",
    "export_governance",
]
