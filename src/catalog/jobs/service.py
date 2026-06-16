"""Job orchestration for API-triggered pipeline operations.

Each job is created PENDING, flipped to RUNNING, then COMPLETED (with a result
summary) or FAILED (with an error message). The work itself is always delegated
to the existing service layer - this module only tracks it. Jobs run
synchronously: the platform is local-first and read-heavy, so a simple, durable,
inspectable record beats a background worker here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..db import connect, init_db
from . import repository as repo
from .models import JOB_TYPES

LOGGER = logging.getLogger(__name__)


class JobError(RuntimeError):
    """Raised when a job cannot be created or run (bad type, disabled, ...)."""


@dataclass(frozen=True)
class JobContext:
    """Paths and flags a job needs, sourced from the API settings."""

    db_path: str = "data/catalog.sqlite"
    cache_dir: str = "cache"
    sources_config: str = "config/sources.yml"
    link_config: str = "config/link_patterns.yml"
    llm_config: str = "config/llm.yml"
    enable_classify: bool = False
    extra: dict = field(default_factory=dict)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- individual job handlers ---------------------------------------------------

def _run_scan(ctx: JobContext, artifact_id: str | None) -> dict:
    from ..scanner import scan

    stats = scan(ctx.sources_config, ctx.db_path, ctx.cache_dir)
    return stats.as_dict()


def _run_extract(ctx: JobContext, artifact_id: str | None) -> dict:
    from ..extraction import extract_all, extract_to_cache, _artifact_from_row

    if artifact_id is None:
        return extract_all(ctx.db_path, ctx.cache_dir)

    with connect(ctx.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE id = ? AND scan_status != 'DELETED' LIMIT 1",
            (artifact_id,),
        ).fetchone()
    if row is None:
        raise JobError(f"No artifact with id {artifact_id!r}.")
    links = extract_to_cache(_artifact_from_row(row), Path(ctx.cache_dir))
    return {"artifact_id": artifact_id, "links_extracted": links, "artifacts_processed": 1}


def _run_discover_links(ctx: JobContext, artifact_id: str | None) -> dict:
    from ..links import discover_links, load_link_config

    stats = discover_links(
        db_path=ctx.db_path,
        cache_dir=ctx.cache_dir,
        config=load_link_config(ctx.link_config),
        artifact_id=artifact_id,
    )
    return stats.as_dict()


def _run_classify(ctx: JobContext, artifact_id: str | None) -> dict:
    if not ctx.enable_classify:
        raise JobError(
            "Classification is disabled. It calls an external LLM provider; enable "
            "it explicitly with 'enable_classify: true' in config/api.yml."
        )
    from ..semantic.config import load_llm_config
    from ..semantic.providers import LLMError, build_provider
    from ..semantic.service import classify_documents

    config = load_llm_config(ctx.llm_config)
    try:
        provider = build_provider(config)
    except LLMError as exc:
        raise JobError(str(exc)) from exc

    stats = classify_documents(
        db_path=ctx.db_path,
        cache_dir=ctx.cache_dir,
        provider=provider,
        artifact_id=artifact_id,
        max_input_chars=config.max_input_chars,
    )
    return {
        "documents_processed": stats.documents_processed,
        "documents_skipped": stats.documents_skipped,
        "errors": stats.errors,
        "entities": stats.entities,
        "capabilities": stats.capabilities,
        "decisions": stats.decisions,
        "risks": stats.risks,
        "relationships": stats.relationships,
    }


def _run_consolidate(ctx: JobContext, artifact_id: str | None) -> dict:
    from ..knowledge.service import consolidate

    return consolidate(ctx.db_path).as_dict()


def _run_compliance_assess(ctx: JobContext, artifact_id: str | None) -> dict:
    from ..compliance.service import assess

    return assess(ctx.db_path).as_dict()


def _run_rescan(ctx: JobContext, artifact_id: str | None) -> dict:
    from ..scanner import scan_file

    if artifact_id is None:
        # A full rescan is just a scan.
        return _run_scan(ctx, None)
    with connect(ctx.db_path) as conn:
        row = conn.execute(
            "SELECT path, source_system FROM artifacts WHERE id = ? "
            "AND scan_status != 'DELETED' LIMIT 1",
            (artifact_id,),
        ).fetchone()
    if row is None:
        raise JobError(f"No artifact with id {artifact_id!r}.")
    if not Path(row["path"]).exists():
        raise JobError(f"Source file no longer on disk: {row['path']}")
    new_id = scan_file(
        row["path"],
        source_system=row["source_system"] or "local_laptop",
        db_path=ctx.db_path,
        cache_dir=ctx.cache_dir,
    )
    return {"artifact_id": new_id, "path": row["path"]}


_HANDLERS = {
    "scan": _run_scan,
    "extract": _run_extract,
    "discover-links": _run_discover_links,
    "classify": _run_classify,
    "consolidate": _run_consolidate,
    "rescan": _run_rescan,
    "compliance-assess": _run_compliance_assess,
}


# -- public API ---------------------------------------------------------------

def run_job(ctx: JobContext, job_type: str, *, artifact_id: str | None = None) -> dict:
    """Create, run, and record a job synchronously. Returns the final job row.

    The job row is persisted at every transition, so even if the work raises the
    failure is captured and visible to a polling client.
    """

    if job_type not in JOB_TYPES:
        raise JobError(
            f"Unknown job type {job_type!r}. Valid types: {', '.join(JOB_TYPES)}"
        )
    handler = _HANDLERS[job_type]
    init_db(ctx.db_path)

    with connect(ctx.db_path) as conn:
        job_id = repo.create_job(conn, job_type=job_type, created_at=_utc_now())
        repo.mark_running(conn, job_id, started_at=_utc_now())
        conn.commit()

    try:
        summary = handler(ctx, artifact_id)
    except Exception as exc:  # noqa: BLE001 - record any failure, never crash the request
        LOGGER.exception("Job %s (%s) failed", job_id, job_type)
        with connect(ctx.db_path) as conn:
            repo.mark_failed(
                conn, job_id, completed_at=_utc_now(), error_message=str(exc)
            )
            conn.commit()
    else:
        with connect(ctx.db_path) as conn:
            repo.mark_completed(
                conn, job_id, completed_at=_utc_now(), result_summary=summary
            )
            conn.commit()

    return get_job(ctx.db_path, job_id)


def get_job(db_path: str, job_id: int) -> dict | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = repo.get_job(conn, job_id)
    return dict(row) if row is not None else None


def list_jobs(
    db_path: str,
    *,
    job_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows, total = repo.list_jobs(
            conn, job_type=job_type, status=status, limit=limit, offset=offset
        )
    return [dict(r) for r in rows], total


__all__ = [
    "JobError",
    "JobContext",
    "run_job",
    "get_job",
    "list_jobs",
]
