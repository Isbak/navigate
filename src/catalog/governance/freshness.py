"""Freshness calculations.

Freshness answers "is this knowledge still current?" purely from how long it has
been since fresh evidence was last seen for an object. The rules are the ones the
spec names and are fully configurable:

    no evidence for >= aging_days   -> AGING
    no evidence for >= stale_days   -> STALE
    archived (by a reviewer)        -> ARCHIVED

The ``freshness_score`` is a continuous companion in ``[0, 1]`` that decays
linearly from 1.0 (seen today) to 0.0 at ``archived_days``, so quality scoring
has a smooth signal rather than only the discrete state.

These functions are pure and deterministic - no clock, no database - which is
what makes them straightforward to test against fixed dates.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .config import FreshnessConfig
from .models import FreshnessState


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def age_in_days(last_seen_at: str | None, now: str | datetime) -> float:
    """Whole-and-fractional days between ``last_seen_at`` and ``now``.

    Returns ``0.0`` when ``last_seen_at`` is missing or unparseable, treating
    the object as just seen rather than infinitely stale.
    """

    seen = _parse(last_seen_at)
    current = now if isinstance(now, datetime) else _parse(now)
    if seen is None or current is None:
        return 0.0
    delta = current - seen
    return max(0.0, delta.total_seconds() / 86400.0)


def freshness_score(age_days: float, config: FreshnessConfig) -> float:
    """Linear decay from 1.0 (age 0) to 0.0 at ``archived_days``."""

    horizon = max(1, config.archived_days)
    return round(max(0.0, min(1.0, 1.0 - age_days / horizon)), 3)


def freshness_for(
    age_days: float,
    config: FreshnessConfig,
    *,
    archived: bool = False,
) -> tuple[str, float]:
    """Return ``(state, score)`` for an object given the age of its evidence.

    ``archived`` is set when a reviewer has explicitly archived the object; that
    decision overrides the age-based state and pins the score to 0.0.
    """

    if archived:
        return FreshnessState.ARCHIVED.value, 0.0

    score = freshness_score(age_days, config)
    if age_days >= config.stale_days:
        state = FreshnessState.STALE.value
    elif age_days >= config.aging_days:
        state = FreshnessState.AGING.value
    else:
        state = FreshnessState.FRESH.value
    return state, score


__all__ = ["age_in_days", "freshness_score", "freshness_for"]
