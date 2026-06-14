"""Bridge API settings to a :class:`catalog.jobs.service.JobContext`."""

from __future__ import annotations

from fastapi import Request

from ..jobs.service import JobContext
from .config import ApiSettings


def build_job_context(request: Request) -> JobContext:
    """FastAPI dependency producing a JobContext from the app settings."""

    settings: ApiSettings = request.app.state.settings
    return JobContext(
        db_path=settings.db_path,
        cache_dir=settings.cache_dir,
        sources_config=settings.sources_config,
        link_config=settings.link_config,
        llm_config=settings.llm_config,
        enable_classify=settings.enable_classify,
    )


__all__ = ["build_job_context"]
