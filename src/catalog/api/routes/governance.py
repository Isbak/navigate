"""Governance endpoints: dashboard, review queue, freshness, orphans, alerts,
quality, domains, change-log feed, and the knowledge-growth trend."""

from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from ...governance import agent_review as agent_review_mod
from ...governance import domains as domain_analysis
from ...governance import ownership
from ...governance import repository as gov_repo
from ...governance import service as gov_service
from ...governance.config import load_governance_config
from ...governance.dashboard import build_dashboard
from ...governance.models import OPEN_REVIEW_STATES, FreshnessState
from ...governance.orphans import all_orphans
from ...knowledge import analytics as know_analytics
from ...knowledge import repository as know_repo
from .. import serializers
from ..config import ApiSettings
from ..dependencies import get_db, get_settings
from ..errors import bad_request, not_found
from ..pagination import Pagination, pagination_params
from ..schemas import (
    ActionResponse,
    AgentApproveRequest,
    AgentApproveResponse,
    AssignOwnerRequest,
    ChangeLogEntry,
    DomainHealth,
    GovernanceAlert,
    GrowthTrend,
    ObjectHistory,
    OwnerAssignment,
    PaginatedResponse,
    QualityItem,
    QualityResponse,
    RevertAgentRequest,
    RevertAgentResponse,
    RevertRequest,
    RevertResponse,
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


@router.get("/domains", response_model=list[DomainHealth])
def domains(
    conn: sqlite3.Connection = Depends(get_db),
) -> list[DomainHealth]:
    """Per-domain governance health: object count, quality, freshness, backlog.

    Domains are discovered from the data: a domain appears once a classified
    document mentions a knowledge object, with no predefined list.
    """

    rows = domain_analysis.domain_health(conn)
    return [serializers.domain_health(d) for d in rows]


@router.get("/domains/{name}", response_model=DomainHealth)
def domain(
    name: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> DomainHealth:
    for d in domain_analysis.domain_health(conn):
        if d["domain"].lower() == name.lower():
            return serializers.domain_health(d)
    raise not_found("Domain not found", domain=name)


@router.get("/changes", response_model=PaginatedResponse[ChangeLogEntry])
def changes(
    conn: sqlite3.Connection = Depends(get_db),
    page: Pagination = Depends(pagination_params),
    object_id: str | None = Query(None),
    change_type: str | None = Query(None),
) -> PaginatedResponse[ChangeLogEntry]:
    """The governance change-log (audit) feed, newest first."""

    rows, total = gov_repo.change_feed(
        conn,
        object_id=object_id,
        change_type=change_type,
        limit=page.limit,
        offset=page.offset,
    )
    items = [serializers.change_entry(r) for r in rows]
    return PaginatedResponse(items=items, limit=page.limit, offset=page.offset, total=total)


@router.get("/growth", response_model=GrowthTrend)
def growth(
    conn: sqlite3.Connection = Depends(get_db),
    interval: Literal["day", "week", "month"] = Query("month"),
    limit: int = Query(12, ge=1, le=365),
) -> GrowthTrend:
    """Knowledge-growth trend: per-period new and cumulative counts of artifacts,
    knowledge objects, and relationships."""

    return GrowthTrend(**know_analytics.growth_trend(conn, interval=interval, limit=limit))


@router.get("/drift", response_model=list[ChangeLogEntry])
def drift(
    conn: sqlite3.Connection = Depends(get_db),
    limit: int = Query(20, ge=1, le=500),
) -> list[ChangeLogEntry]:
    """Detected knowledge drift (confidence/attribute shifts), newest first."""

    return [serializers.change_entry(r) for r in gov_repo.drift_findings(conn, limit)]


@router.get("/owners", response_model=list[OwnerAssignment])
def owners(conn: sqlite3.Connection = Depends(get_db)) -> list[OwnerAssignment]:
    """Every object→owner assignment (Team / Person / Domain)."""

    return [serializers.owner_assignment(r) for r in gov_repo.all_owners(conn)]


@router.get("/objects/{object_id}/history", response_model=ObjectHistory)
def object_history(
    object_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> ObjectHistory:
    """Combined audit view: change log, lifecycle state, and current owner."""

    _require_object(conn, object_id)
    lifecycle = gov_repo.get_lifecycle(conn, object_id)
    owner = gov_repo.get_owner(conn, object_id)
    return ObjectHistory(
        object_id=object_id,
        changes=[
            serializers.change_entry(r) for r in gov_repo.changes_for_object(conn, object_id)
        ],
        lifecycle=dict(lifecycle) if lifecycle is not None else None,
        owner=serializers.owner_assignment(owner) if owner is not None else None,
    )


@router.post("/objects/{object_id}/assign-owner", response_model=ActionResponse)
def assign_owner(
    object_id: str,
    request: AssignOwnerRequest,
    settings: ApiSettings = Depends(get_settings),
) -> ActionResponse:
    """Assign an owner (Team / Person / Domain) to an object."""

    try:
        changed = ownership.assign_owner(
            settings.db_path,
            object_id,
            request.owner_type,
            request.owner_id,
            assigned_by="api",
        )
    except ValueError as exc:
        raise bad_request(str(exc), owner_type=request.owner_type) from exc
    if not changed:
        raise not_found("Knowledge object not found", object_id=object_id)
    owner = f"{request.owner_type}:{request.owner_id}"
    return ActionResponse(
        id=object_id, status="OWNED", message=f"Object {object_id} owner -> {owner}"
    )


@router.post("/objects/{object_id}/flag", response_model=ActionResponse)
def flag(
    object_id: str,
    settings: ApiSettings = Depends(get_settings),
) -> ActionResponse:
    """Flag an object as needing attention without changing its review status."""

    changed = gov_service.flag_object(settings.db_path, object_id, reviewer="api")
    if not changed:
        raise not_found("Knowledge object not found", object_id=object_id)
    return ActionResponse(
        id=object_id, status="NEEDS_ATTENTION", message=f"Object {object_id} flagged"
    )


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


@router.post("/agent-approve", response_model=AgentApproveResponse)
def agent_approve(
    request: AgentApproveRequest,
    settings: ApiSettings = Depends(get_settings),
) -> AgentApproveResponse:
    """Approve eligible PROPOSED items under the agent-review policy.

    Decisions are tagged ``agent:<name>`` and are reversible via ``/revert`` and
    ``/revert-agent``. Use ``dry_run`` to preview the candidates without writing.
    """

    policy = load_governance_config(settings.governance_config).agent_review
    overrides: dict[str, Any] = {}
    if request.agent is not None:
        overrides["agent_name"] = request.agent
    if request.min_confidence is not None:
        overrides["min_confidence"] = request.min_confidence
    if request.max_confidence is not None:
        overrides["max_confidence"] = request.max_confidence
    if overrides:
        policy = dataclasses.replace(policy, **overrides)

    try:
        stats = agent_review_mod.agent_approve(
            settings.db_path,
            config=policy,
            target=request.target,
            note=request.note,
            dry_run=request.dry_run,
        )
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    return AgentApproveResponse(**stats.as_dict())


@router.post("/revert", response_model=RevertResponse)
def revert(
    request: RevertRequest,
    settings: ApiSettings = Depends(get_settings),
) -> RevertResponse:
    """Undo the latest review decision on one target, back to its prior state."""

    result = agent_review_mod.revert_review(
        settings.db_path,
        request.target_kind,
        request.target_id,
        reviewer="api",
        note=request.note,
    )
    return RevertResponse(**result.as_dict())


@router.post("/revert-agent", response_model=RevertAgentResponse)
def revert_agent(
    request: RevertAgentRequest,
    settings: ApiSettings = Depends(get_settings),
) -> RevertAgentResponse:
    """Bulk-undo agent decisions, never overriding a later human decision."""

    stats = agent_review_mod.revert_agent_actions(
        settings.db_path,
        agent=request.agent,
        since=request.since,
        reviewer="api",
        note=request.note,
    )
    return RevertAgentResponse(
        reverted=stats.reverted,
        skipped=stats.skipped,
        results=[RevertResponse(**r.as_dict()) for r in stats.results],
    )


def _require_object(conn: sqlite3.Connection, object_id: str) -> None:
    if know_repo.get_object(conn, object_id) is None:
        raise not_found("Knowledge object not found", object_id=object_id)
