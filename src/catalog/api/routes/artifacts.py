"""Artifact endpoints: listing, detail, related links/evidence, and re-processing."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from ...jobs.service import JobContext, run_job
from ...links import repository as link_repo
from .. import repository as repo
from .. import serializers
from ..config import ApiSettings
from ..dependencies import build_job_context, get_db, get_settings
from ..errors import not_found
from ..pagination import Pagination, pagination_params
from ..schemas import Artifact, Evidence, Job, Link, PaginatedResponse

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("", response_model=PaginatedResponse[Artifact])
def list_artifacts(
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
    page: Pagination = Depends(pagination_params),
    file_type: str | None = Query(None),
    scan_status: str | None = Query(None),
    extraction_status: str | None = Query(None),
    classification_status: str | None = Query(None),
    search: str | None = Query(None),
) -> PaginatedResponse[Artifact]:
    extracted = repo.extracted_artifact_ids(settings.cache_dir)
    classified = repo.classified_artifact_ids(conn)
    rows, total = repo.list_artifacts(
        conn,
        file_type=file_type,
        scan_status=scan_status,
        extraction_status=extraction_status,
        classification_status=classification_status,
        search=search,
        extracted_ids=extracted,
        classified_ids=classified,
        limit=page.limit,
        offset=page.offset,
    )
    items = [
        serializers.artifact(r, extracted_ids=extracted, classified_ids=classified)
        for r in rows
    ]
    return PaginatedResponse(items=items, limit=page.limit, offset=page.offset, total=total)


@router.get("/{artifact_id}", response_model=Artifact)
def get_artifact(
    artifact_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> Artifact:
    row = repo.get_artifact(conn, artifact_id)
    if row is None:
        raise not_found("Artifact not found", artifact_id=artifact_id)
    extracted = repo.extracted_artifact_ids(settings.cache_dir)
    classified = repo.classified_artifact_ids(conn)
    return serializers.artifact(row, extracted_ids=extracted, classified_ids=classified)


@router.get("/{artifact_id}/links", response_model=PaginatedResponse[Link])
def artifact_links(
    artifact_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> PaginatedResponse[Link]:
    _require_artifact(conn, artifact_id)
    rows = link_repo.links_for_artifact(conn, artifact_id)
    items = [serializers.link(r) for r in rows]
    return PaginatedResponse(items=items, limit=len(items), offset=0, total=len(items))


@router.get("/{artifact_id}/evidence", response_model=PaginatedResponse[Evidence])
def artifact_evidence(
    artifact_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> PaginatedResponse[Evidence]:
    _require_artifact(conn, artifact_id)
    rows = repo.evidence_for_artifact(conn, artifact_id)
    items = [serializers.evidence(r) for r in rows]
    return PaginatedResponse(items=items, limit=len(items), offset=0, total=len(items))


@router.post("/{artifact_id}/rescan", response_model=Job)
def rescan_artifact(
    artifact_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    ctx: JobContext = Depends(build_job_context),
) -> Job:
    _require_artifact(conn, artifact_id)
    return serializers.job(run_job(ctx, "rescan", artifact_id=artifact_id))


@router.post("/{artifact_id}/extract", response_model=Job)
def extract_artifact(
    artifact_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    ctx: JobContext = Depends(build_job_context),
) -> Job:
    _require_artifact(conn, artifact_id)
    return serializers.job(run_job(ctx, "extract", artifact_id=artifact_id))


@router.post("/{artifact_id}/classify", response_model=Job)
def classify_artifact(
    artifact_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    ctx: JobContext = Depends(build_job_context),
) -> Job:
    _require_artifact(conn, artifact_id)
    return serializers.job(run_job(ctx, "classify", artifact_id=artifact_id))


def _require_artifact(conn: sqlite3.Connection, artifact_id: str) -> None:
    if repo.get_artifact(conn, artifact_id) is None:
        raise not_found("Artifact not found", artifact_id=artifact_id)
