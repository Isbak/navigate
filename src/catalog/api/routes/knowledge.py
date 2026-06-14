"""Knowledge-object endpoints: listing, detail, related data, and review actions."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from ...governance import service as gov_service
from ...knowledge import repository as know_repo
from .. import repository as repo
from .. import serializers
from ..config import ApiSettings
from ..dependencies import get_db, get_settings
from ..errors import not_found
from ..pagination import Pagination, pagination_params
from ..schemas import (
    ActionResponse,
    Evidence,
    KnowledgeObject,
    Mention,
    PaginatedResponse,
    Relationship,
)

router = APIRouter(prefix="/knowledge-objects", tags=["knowledge"])


@router.get("", response_model=PaginatedResponse[KnowledgeObject])
def list_objects(
    conn: sqlite3.Connection = Depends(get_db),
    page: Pagination = Depends(pagination_params),
    object_type: str | None = Query(None),
    status: str | None = Query(None),
    review_status: str | None = Query(None),
    owner: str | None = Query(None),
    domain: str | None = Query(None),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    search: str | None = Query(None),
) -> PaginatedResponse[KnowledgeObject]:
    rows, total = repo.list_knowledge_objects(
        conn,
        object_type=object_type,
        status=status,
        review_status=review_status,
        owner=owner,
        domain=domain,
        min_confidence=min_confidence,
        search=search,
        limit=page.limit,
        offset=page.offset,
    )
    items = [serializers.knowledge_object(r) for r in rows]
    return PaginatedResponse(items=items, limit=page.limit, offset=page.offset, total=total)


@router.get("/{object_id}", response_model=KnowledgeObject)
def get_object(
    object_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> KnowledgeObject:
    row = repo.get_knowledge_object(conn, object_id)
    if row is None:
        raise not_found("Knowledge object not found", object_id=object_id)
    return serializers.knowledge_object(row)


@router.get("/{object_id}/relationships", response_model=PaginatedResponse[Relationship])
def object_relationships(
    object_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> PaginatedResponse[Relationship]:
    _require_object(conn, object_id)
    rows = know_repo.relationships_for_object(conn, object_id)
    items = [serializers.relationship(r) for r in rows]
    return PaginatedResponse(items=items, limit=len(items), offset=0, total=len(items))


@router.get("/{object_id}/evidence", response_model=PaginatedResponse[Evidence])
def object_evidence(
    object_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> PaginatedResponse[Evidence]:
    _require_object(conn, object_id)
    rows = know_repo.evidence_for_object(conn, object_id)
    items = [serializers.evidence(r) for r in rows]
    return PaginatedResponse(items=items, limit=len(items), offset=0, total=len(items))


@router.get("/{object_id}/mentions", response_model=PaginatedResponse[Mention])
def object_mentions(
    object_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> PaginatedResponse[Mention]:
    _require_object(conn, object_id)
    rows = know_repo.mentions_for_object(conn, object_id)
    items = [serializers.mention(r) for r in rows]
    return PaginatedResponse(items=items, limit=len(items), offset=0, total=len(items))


@router.post("/{object_id}/approve", response_model=ActionResponse)
def approve_object(
    object_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> ActionResponse:
    return _review(settings, object_id, gov_service.approve_object, "APPROVED")


@router.post("/{object_id}/reject", response_model=ActionResponse)
def reject_object(
    object_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> ActionResponse:
    return _review(settings, object_id, gov_service.reject_object, "REJECTED")


@router.post("/{object_id}/archive", response_model=ActionResponse)
def archive_object(
    object_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> ActionResponse:
    return _review(settings, object_id, gov_service.archive_object, "ARCHIVED")


def _review(settings, object_id, action, label) -> ActionResponse:
    changed = action(settings.db_path, object_id, reviewer="api")
    if not changed:
        raise not_found("Knowledge object not found", object_id=object_id)
    return ActionResponse(
        id=object_id, status=label, message=f"Object {object_id} -> {label}"
    )


def _require_object(conn: sqlite3.Connection, object_id: str) -> None:
    if know_repo.get_object(conn, object_id) is None:
        raise not_found("Knowledge object not found", object_id=object_id)
