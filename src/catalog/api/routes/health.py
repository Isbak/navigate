"""Health and aggregate-stats endpoints."""

from __future__ import annotations

import sqlite3
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Depends

from ...db import latest_scan_run
from ...knowledge import repository as know_repo
from ...links import repository as link_repo
from ..config import ApiSettings
from ..dependencies import get_db, get_settings
from ..schemas import HealthResponse, StatsResponse

router = APIRouter(tags=["health"])


def _version() -> str:
    try:
        return pkg_version("knowledge-catalog")
    except PackageNotFoundError:  # pragma: no cover - installed in normal use
        return "0.0.0"


@router.get("/health", response_model=HealthResponse)
def health(
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> HealthResponse:
    """Liveness plus a database-connectivity probe and the package version."""

    database: dict = {"path": settings.db_path}
    try:
        conn.execute("SELECT 1").fetchone()
        database["connected"] = True
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        database["connected"] = False
        database["error"] = str(exc)
    status = "ok" if database["connected"] else "degraded"
    return HealthResponse(status=status, database=database, version=_version())


@router.get("/stats", response_model=StatsResponse)
def stats(conn: sqlite3.Connection = Depends(get_db)) -> StatsResponse:
    """Top-level counts across the whole knowledge platform."""

    artifact_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE scan_status != 'DELETED'"
        ).fetchone()[0]
    )
    pending = int(
        conn.execute(
            "SELECT COUNT(*) FROM knowledge_objects WHERE status = 'PROPOSED'"
        ).fetchone()[0]
    )
    stale = int(
        conn.execute(
            "SELECT COUNT(*) FROM knowledge_lifecycle "
            "WHERE freshness_state IN ('STALE', 'ARCHIVED')"
        ).fetchone()[0]
    )
    run = latest_scan_run(conn)
    return StatsResponse(
        artifact_count=artifact_count,
        link_count=link_repo.count_links(conn),
        knowledge_object_count=know_repo.count_objects(conn),
        relationship_count=know_repo.count_table(conn, "knowledge_relationships"),
        evidence_count=know_repo.count_table(conn, "knowledge_evidence"),
        pending_review_count=pending,
        stale_object_count=stale,
        last_scan=dict(run) if run is not None else None,
    )
