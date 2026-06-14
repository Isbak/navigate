"""Link endpoints: listing, aggregate stats, and most-referenced targets."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from ...links import repository as link_repo
from .. import repository as repo
from .. import serializers
from ..dependencies import get_db
from ..pagination import Pagination, pagination_params
from ..schemas import CountItem, Link, LinkStats, PaginatedResponse, TopTarget

router = APIRouter(prefix="/links", tags=["links"])


@router.get("", response_model=PaginatedResponse[Link])
def list_links(
    conn: sqlite3.Connection = Depends(get_db),
    page: Pagination = Depends(pagination_params),
    source_artifact_id: str | None = Query(None),
    target_system: str | None = Query(None),
    target_type: str | None = Query(None),
    link_kind: str | None = Query(None),
    status: str | None = Query(None),
) -> PaginatedResponse[Link]:
    rows, total = repo.list_links(
        conn,
        source_artifact_id=source_artifact_id,
        target_system=target_system,
        target_type=target_type,
        link_kind=link_kind,
        status=status,
        limit=page.limit,
        offset=page.offset,
    )
    items = [serializers.link(r) for r in rows]
    return PaginatedResponse(items=items, limit=page.limit, offset=page.offset, total=total)


@router.get("/stats", response_model=LinkStats)
def link_stats(conn: sqlite3.Connection = Depends(get_db)) -> LinkStats:
    def counts(column: str) -> list[CountItem]:
        return [
            CountItem(key=r["key"], count=r["count"])
            for r in link_repo.counts_by(conn, column)
        ]

    return LinkStats(
        total=link_repo.count_links(conn),
        by_target_system=counts("target_system"),
        by_target_type=counts("target_type"),
        by_link_kind=counts("link_kind"),
    )


@router.get("/top-targets", response_model=list[TopTarget])
def top_targets(
    conn: sqlite3.Connection = Depends(get_db),
    limit: int = Query(20, ge=1, le=200),
) -> list[TopTarget]:
    return [
        TopTarget(url=r["key"], count=r["count"])
        for r in link_repo.top_referenced_urls(conn, limit)
    ]
