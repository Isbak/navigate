"""Job endpoints: trigger pipeline operations and inspect their status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...jobs.service import JobContext, list_jobs, run_job
from ...jobs.service import get_job as get_job_record
from .. import serializers
from ..config import ApiSettings
from ..dependencies import build_job_context, get_settings
from ..errors import not_found
from ..pagination import Pagination, pagination_params
from ..schemas import Job, PaginatedResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _trigger(ctx: JobContext, job_type: str) -> Job:
    return serializers.job(run_job(ctx, job_type))


@router.post("/scan", response_model=Job)
def scan(ctx: JobContext = Depends(build_job_context)) -> Job:
    return _trigger(ctx, "scan")


@router.post("/extract", response_model=Job)
def extract(ctx: JobContext = Depends(build_job_context)) -> Job:
    return _trigger(ctx, "extract")


@router.post("/discover-links", response_model=Job)
def discover_links(ctx: JobContext = Depends(build_job_context)) -> Job:
    return _trigger(ctx, "discover-links")


@router.post("/classify", response_model=Job)
def classify(ctx: JobContext = Depends(build_job_context)) -> Job:
    return _trigger(ctx, "classify")


@router.post("/consolidate", response_model=Job)
def consolidate(ctx: JobContext = Depends(build_job_context)) -> Job:
    return _trigger(ctx, "consolidate")


@router.get("", response_model=PaginatedResponse[Job])
def list_all_jobs(
    settings: ApiSettings = Depends(get_settings),
    page: Pagination = Depends(pagination_params),
    job_type: str | None = Query(None),
    status: str | None = Query(None),
) -> PaginatedResponse[Job]:
    rows, total = list_jobs(
        settings.db_path,
        job_type=job_type,
        status=status,
        limit=page.limit,
        offset=page.offset,
    )
    items = [serializers.job(r) for r in rows]
    return PaginatedResponse(items=items, limit=page.limit, offset=page.offset, total=total)


@router.get("/{job_id}", response_model=Job)
def get_job(
    job_id: int, settings: ApiSettings = Depends(get_settings)
) -> Job:
    row = get_job_record(settings.db_path, job_id)
    if row is None:
        raise not_found("Job not found", job_id=job_id)
    return serializers.job(row)
