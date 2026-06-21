"""Tests for the governance ingestion scheduler (Prompt #10)."""

from datetime import UTC, datetime, timedelta

from catalog.governance.ingestion import (
    read_last_run,
    run_ingestion,
    should_run,
)

NOW = datetime(2026, 6, 13, tzinfo=UTC)


def test_manual_always_runs():
    assert should_run("manual", None, NOW) is True
    assert should_run("manual", NOW.isoformat(), NOW) is True


def test_daily_due_after_a_day():
    yesterday = (NOW - timedelta(days=1, hours=1)).isoformat()
    this_morning = (NOW - timedelta(hours=2)).isoformat()
    assert should_run("daily", yesterday, NOW) is True
    assert should_run("daily", this_morning, NOW) is False


def test_weekly_due_after_a_week():
    eight_days = (NOW - timedelta(days=8)).isoformat()
    three_days = (NOW - timedelta(days=3)).isoformat()
    assert should_run("weekly", eight_days, NOW) is True
    assert should_run("weekly", three_days, NOW) is False


def test_first_run_always_due():
    assert should_run("daily", None, NOW) is True


def test_run_ingestion_executes_steps_and_records(tmp_path):
    db = str(tmp_path / "c.sqlite")
    calls = []
    steps = [
        ("a", lambda: calls.append("a") or "ok-a"),
        ("b", lambda: calls.append("b") or "ok-b"),
    ]
    result = run_ingestion(db, steps, schedule="manual", force=True)
    assert result.ran
    assert result.ok
    assert calls == ["a", "b"]
    assert read_last_run(db) is not None


def test_run_ingestion_skips_when_not_due(tmp_path):
    db = str(tmp_path / "c.sqlite")
    run_ingestion(db, [("a", lambda: "ok")], schedule="weekly", force=True)
    # Immediately running again on a weekly cadence is not due.
    result = run_ingestion(db, [("a", lambda: "ok")], schedule="weekly", force=False)
    assert result.ran is False
    assert "not due" in result.skipped_reason


def test_failed_step_does_not_abort_run(tmp_path):
    db = str(tmp_path / "c.sqlite")

    def boom():
        raise RuntimeError("kaboom")

    ran = []
    steps = [("first", boom), ("second", lambda: ran.append("second"))]
    result = run_ingestion(db, steps, schedule="manual", force=True)
    assert result.ran
    assert result.ok is False
    assert ran == ["second"]  # the run continued past the failure
    assert result.steps[0].ok is False
    assert "kaboom" in result.steps[0].detail
