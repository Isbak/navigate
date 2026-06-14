"""Controlled vocabulary for the job tracker."""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    """Lifecycle of a tracked job."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


# The pipeline operations the API is allowed to trigger as background jobs.
JOB_TYPES = (
    "scan",
    "extract",
    "discover-links",
    "classify",
    "consolidate",
    "rescan",
)


__all__ = ["JobStatus", "JOB_TYPES"]
