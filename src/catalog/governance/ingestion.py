"""Automated ingestion scheduling.

Continuous knowledge operations means the pipeline runs on a cadence, not by
hand. This module implements a light scheduler: a pure ``should_run`` decision
(daily / weekly / manual against a last-run marker) and a ``run_ingestion``
driver that executes the pipeline steps in order and records when it last ran.

The pipeline is:

    scan -> extract -> discover-links -> classify -> consolidate
         -> rdf-export -> (optional) fuseki-load -> governance scan

Steps are passed in as ``(name, callable)`` pairs so the driver stays decoupled
from the rest of the system (and testable without touching the network or an
LLM). A step that raises is recorded as failed and, unless ``stop_on_error`` is
set, the run continues - one flaky step should not abort the whole cadence.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_SCHEDULE_INTERVALS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def should_run(schedule: str, last_run: str | None, now: str | datetime) -> bool:
    """Decide whether the cadence is due.

    ``manual`` always runs (the operator asked for it); ``daily`` / ``weekly``
    run only once their interval has elapsed since ``last_run``. An unknown
    schedule is treated as manual.
    """

    schedule = (schedule or "manual").lower()
    if schedule not in _SCHEDULE_INTERVALS:
        return True  # manual / unknown -> run on request
    previous = _parse(last_run)
    if previous is None:
        return True
    current = now if isinstance(now, datetime) else _parse(now)
    if current is None:
        return True
    return (current - previous) >= _SCHEDULE_INTERVALS[schedule]


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class IngestionResult:
    ran: bool
    schedule: str
    steps: list[StepResult] = field(default_factory=list)
    skipped_reason: str = ""

    @property
    def ok(self) -> bool:
        return self.ran and all(s.ok for s in self.steps)


def _marker_path(db_path: str | Path) -> Path:
    return Path(db_path).parent / "governance_ingest.json"


def read_last_run(db_path: str | Path) -> str | None:
    path = _marker_path(db_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("last_run")
    except (ValueError, OSError):
        return None


def _write_last_run(db_path: str | Path, when: str) -> None:
    path = _marker_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_run": when}, indent=2), encoding="utf-8")


def run_ingestion(
    db_path: str | Path,
    steps: list[tuple[str, Callable[[], object]]],
    *,
    schedule: str = "manual",
    force: bool = False,
    stop_on_error: bool = False,
    now: str | None = None,
) -> IngestionResult:
    """Run the pipeline if due (or forced), recording each step's outcome."""

    when = now or datetime.now(UTC).isoformat()
    last_run = read_last_run(db_path)
    if not force and not should_run(schedule, last_run, when):
        return IngestionResult(
            ran=False,
            schedule=schedule,
            skipped_reason=f"not due (last run {last_run})",
        )

    result = IngestionResult(ran=True, schedule=schedule)
    for name, step in steps:
        try:
            outcome = step()
            result.steps.append(StepResult(name=name, ok=True, detail=str(outcome or "")))
        except Exception as exc:  # noqa: BLE001 - one step must not abort the cadence
            LOGGER.warning("Ingestion step %s failed: %s", name, exc)
            result.steps.append(StepResult(name=name, ok=False, detail=str(exc)))
            if stop_on_error:
                break

    _write_last_run(db_path, when)
    return result


__all__ = [
    "should_run",
    "StepResult",
    "IngestionResult",
    "read_last_run",
    "run_ingestion",
]
