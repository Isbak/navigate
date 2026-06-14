"""Job tracking for API-triggered pipeline operations.

The REST API exposes the heavy pipeline steps (scan, extract, discover-links,
classify, consolidate) as *jobs* so a client can trigger them and then poll for
completion. This package owns the persistence (``repository``) and the
orchestration (``service``) for those jobs. The actual work is always delegated
to the existing service layer - jobs add tracking, never new business logic.
"""

from __future__ import annotations

from .models import JOB_TYPES, JobStatus
from .service import JobError, get_job, list_jobs, run_job

__all__ = [
    "JOB_TYPES",
    "JobStatus",
    "JobError",
    "get_job",
    "list_jobs",
    "run_job",
]
