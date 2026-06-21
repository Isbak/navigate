"""Tests for governance freshness calculations (Prompt #10)."""

from datetime import UTC, datetime, timedelta

from catalog.governance.config import FreshnessConfig
from catalog.governance.freshness import age_in_days, freshness_for, freshness_score
from catalog.governance.models import FreshnessState

CONFIG = FreshnessConfig(aging_days=180, stale_days=365, archived_days=730)


def test_recent_evidence_is_fresh():
    state, score = freshness_for(10.0, CONFIG)
    assert state == FreshnessState.FRESH.value
    assert score > 0.9


def test_180_days_is_aging():
    # The spec rule: no evidence for 180 days -> AGING.
    state, _ = freshness_for(180.0, CONFIG)
    assert state == FreshnessState.AGING.value


def test_just_under_aging_is_still_fresh():
    state, _ = freshness_for(179.0, CONFIG)
    assert state == FreshnessState.FRESH.value


def test_365_days_is_stale():
    # The spec rule: no evidence for 365 days -> STALE.
    state, _ = freshness_for(365.0, CONFIG)
    assert state == FreshnessState.STALE.value


def test_archived_overrides_age():
    state, score = freshness_for(5.0, CONFIG, archived=True)
    assert state == FreshnessState.ARCHIVED.value
    assert score == 0.0


def test_score_decays_to_zero_at_horizon():
    assert freshness_score(0.0, CONFIG) == 1.0
    assert freshness_score(730.0, CONFIG) == 0.0
    assert freshness_score(1000.0, CONFIG) == 0.0
    mid = freshness_score(365.0, CONFIG)
    assert 0.4 < mid < 0.6


def test_age_in_days_between_dates():
    now = datetime(2026, 6, 13, tzinfo=UTC)
    seen = (now - timedelta(days=200)).isoformat()
    assert round(age_in_days(seen, now)) == 200


def test_age_in_days_missing_is_zero():
    assert age_in_days(None, datetime.now(UTC)) == 0.0
    assert age_in_days("", datetime.now(UTC)) == 0.0


def test_age_handles_naive_timestamp():
    now = datetime(2026, 6, 13, tzinfo=UTC)
    naive = "2026-01-01T00:00:00"  # no timezone
    assert age_in_days(naive, now) > 150
