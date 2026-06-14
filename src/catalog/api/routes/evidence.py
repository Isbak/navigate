"""Evidence endpoints: listing and detail."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from .. import repository as repo
from .. import serializers
from ..dependencies import get_db
from ..errors import not_found
from ..pagination import Pagination, pagination_params
from ..schemas import Evidence, PaginatedResponse

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get("", response_model=PaginatedResponse[Evidence])
def list_evidence(
    conn: sqlite3.Connection = Depends(get_db),
    page: Pagination = Depends(pagination_params),
    artifact_id: str | None = Query(None),
    knowledge_object_id: str | None = Query(None),
    relationship_id: int | None = Query(None),
) -> PaginatedResponse[Evidence]:
    rows, total = repo.list_evidence(
        conn,
        artifact_id=artifact_id,
        knowledge_object_id=knowledge_object_id,
        relationship_id=relationship_id,
        limit=page.limit,
        offset=page.offset,
    )
    items = [serializers.evidence(r) for r in rows]
    return PaginatedResponse(items=items, limit=page.limit, offset=page.offset, total=total)


@router.get("/{evidence_id}", response_model=Evidence)
def get_evidence(
    evidence_id: int, conn: sqlite3.Connection = Depends(get_db)
) -> Evidence:
    row = repo.get_evidence(conn, evidence_id)
    if row is None:
        raise not_found("Evidence not found", evidence_id=evidence_id)
    return serializers.evidence(row)
