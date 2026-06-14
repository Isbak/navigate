"""Relationship endpoints: listing, detail, and review actions."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ...knowledge.models import ReviewState
from ...knowledge.service import approve_relationships_by_confidence, review_relationship
from .. import repository as repo
from .. import serializers
from ..config import ApiSettings
from ..dependencies import get_db, get_settings
from ..errors import not_found
from ..pagination import Pagination, pagination_params
from ..schemas import (
    ActionResponse,
    ConfidenceApprovalRequest,
    ConfidenceApprovalResponse,
    PaginatedResponse,
    Relationship,
)

router = APIRouter(prefix="/relationships", tags=["relationships"])


@router.get("", response_model=PaginatedResponse[Relationship])
def list_relationships(
    conn: sqlite3.Connection = Depends(get_db),
    page: Pagination = Depends(pagination_params),
    source_object_id: str | None = Query(None),
    target_object_id: str | None = Query(None),
    predicate: str | None = Query(None),
    review_status: str | None = Query(None),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
) -> PaginatedResponse[Relationship]:
    rows, total = repo.list_relationships(
        conn,
        source_object_id=source_object_id,
        target_object_id=target_object_id,
        predicate=predicate,
        review_status=review_status,
        min_confidence=min_confidence,
        limit=page.limit,
        offset=page.offset,
    )
    items = [serializers.relationship(r) for r in rows]
    return PaginatedResponse(
        items=items, limit=page.limit, offset=page.offset, total=total
    )


@router.post("/approve-confidence", response_model=ConfidenceApprovalResponse)
def approve_relationships_by_confidence_interval(
    request: ConfidenceApprovalRequest,
    settings: ApiSettings = Depends(get_settings),
) -> ConfidenceApprovalResponse:
    if request.min_confidence > request.max_confidence:
        raise HTTPException(
            status_code=400,
            detail="min_confidence must be less than or equal to max_confidence",
        )
    statuses = [ReviewState.PROPOSED.value]
    if request.include_reviewed:
        statuses.append(ReviewState.REVIEWED.value)
    approved = 0
    for status in statuses:
        stats = approve_relationships_by_confidence(
            settings.db_path,
            request.min_confidence,
            request.max_confidence,
            reviewer="api",
            note=request.note,
            current_status=status,
        )
        approved += stats.relationships_approved
    return ConfidenceApprovalResponse(
        min_confidence=request.min_confidence,
        max_confidence=request.max_confidence,
        relationships_approved=approved,
        message=f"Approved {approved} relationships",
    )


@router.get("/{relationship_id}", response_model=Relationship)
def get_relationship(
    relationship_id: int, conn: sqlite3.Connection = Depends(get_db)
) -> Relationship:
    row = repo.get_relationship(conn, relationship_id)
    if row is None:
        raise not_found("Relationship not found", relationship_id=relationship_id)
    return serializers.relationship(row)


@router.post("/{relationship_id}/approve", response_model=ActionResponse)
def approve_relationship(
    relationship_id: int, settings: ApiSettings = Depends(get_settings)
) -> ActionResponse:
    return _review(settings, relationship_id, ReviewState.APPROVED.value)


@router.post("/{relationship_id}/reject", response_model=ActionResponse)
def reject_relationship(
    relationship_id: int, settings: ApiSettings = Depends(get_settings)
) -> ActionResponse:
    return _review(settings, relationship_id, ReviewState.REJECTED.value)


@router.post("/{relationship_id}/archive", response_model=ActionResponse)
def archive_relationship(
    relationship_id: int, settings: ApiSettings = Depends(get_settings)
) -> ActionResponse:
    return _review(settings, relationship_id, ReviewState.ARCHIVED.value)


def _review(settings, relationship_id: int, status: str) -> ActionResponse:
    changed = review_relationship(
        settings.db_path, relationship_id, status, reviewer="api"
    )
    if not changed:
        raise not_found("Relationship not found", relationship_id=relationship_id)
    return ActionResponse(
        id=str(relationship_id),
        status=status,
        message=f"Relationship {relationship_id} -> {status}",
    )
