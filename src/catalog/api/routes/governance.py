"""Governance endpoints: dashboard, review queue, freshness, orphans, alerts, quality."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query

from ...governance import repository as gov_repo
from ...governance.config import load_governance_config
from ...governance.dashboard import build_dashboard
from ...governance.models import FreshnessState, OPEN_REVIEW_STATES
from ...governance.orphans import all_orphans
from .. import serializers
from ..config import ApiSettings
from ..dependencies import get_db, get_settings
from ..schemas import (
    GovernanceAlert,
    QualityItem,
    QualityResponse,
    ReviewQueueItem,
    StaleItem,
)

router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/dashboard", response_model=dict)
def dashboard(
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> dict[str, Any]:
    config = load_governance_config(settings.governance_config)
    return build_dashboard(conn, config)


@router.get("/review-queue", response_model=list[ReviewQueueItem])
def review_queue(conn: sqlite3.Connection = Depends(get_db)) -> list[ReviewQueueItem]:
    rows = gov_repo.lifecycle_by_review(conn, OPEN_REVIEW_STATES)
    return [
        ReviewQueueItem(
            object_id=r["object_id"],
            name=r["name"],
            object_type=r["object_type"],
            review_state=r["review_state"],
            freshness_state=r["freshness_state"],
            last_confidence=r["last_confidence"],
        )
        for r in rows
    ]


@router.get("/stale", response_model=list[StaleItem])
def stale(conn: sqlite3.Connection = Depends(get_db)) -> list[StaleItem]:
    states = (FreshnessState.STALE.value, FreshnessState.ARCHIVED.value)
    rows = gov_repo.lifecycle_by_freshness(conn, states)
    return [
        StaleItem(
            object_id=r["object_id"],
            name=r["name"],
            object_type=r["object_type"],
            freshness_state=r["freshness_state"],
            freshness_score=r["freshness_score"],
            last_seen_at=r["last_seen_at"],
        )
        for r in rows
    ]


@router.get("/orphaned", response_model=dict)
def orphaned(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return all_orphans(conn)


@router.get("/alerts", response_model=list[GovernanceAlert])
def alerts(
    conn: sqlite3.Connection = Depends(get_db),
    alert_type: str | None = Query(None),
    severity: str | None = Query(None),
) -> list[GovernanceAlert]:
    rows = gov_repo.open_alerts(conn, alert_type)
    items = [serializers.alert(r) for r in rows]
    if severity:
        items = [a for a in items if a.severity == severity]
    return items


@router.get("/quality", response_model=QualityResponse)
def quality(
    conn: sqlite3.Connection = Depends(get_db),
    ascending: bool = Query(False, description="rank lowest-quality first"),
) -> QualityResponse:
    rows = gov_repo.quality_ranked(conn, ascending=ascending)
    items = [
        QualityItem(
            object_id=r["object_id"],
            canonical_name=r["canonical_name"],
            object_type=r["object_type"],
            quality_score=r["quality_score"],
            evidence_count=r["evidence_count"],
            document_count=r["document_count"],
        )
        for r in rows
    ]
    return QualityResponse(average_quality=gov_repo.average_quality(conn), items=items)
