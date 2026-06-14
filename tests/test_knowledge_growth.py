"""Unit tests for the knowledge-growth trend analytics."""

from __future__ import annotations

import pytest

from catalog.db import connect, init_db
from catalog.knowledge import analytics


def _object(conn, oid: str, created_at: str) -> None:
    conn.execute(
        "INSERT INTO knowledge_objects(id, name, object_type, created_at) "
        "VALUES (?, ?, 'Capability', ?)",
        (oid, oid, created_at),
    )


def _seed(tmp_path) -> str:
    db = str(tmp_path / "catalog.sqlite")
    init_db(db)
    with connect(db) as conn:
        _object(conn, "a", "2026-01-15T00:00:00+00:00")
        _object(conn, "b", "2026-01-20T00:00:00+00:00")
        _object(conn, "c", "2026-03-02T00:00:00+00:00")
        conn.commit()
    return db


def test_growth_trend_buckets_and_accumulates(tmp_path):
    db = _seed(tmp_path)
    with connect(db) as conn:
        trend = analytics.growth_trend(conn, interval="month", limit=12)

    points = {p["period"]: p for p in trend["points"]}
    assert trend["interval"] == "month"
    assert points["2026-01"]["objects_added"] == 2
    assert points["2026-01"]["objects_total"] == 2
    # No rows in February, so it is not a period at all.
    assert "2026-02" not in points
    # The cumulative total carries forward across the empty month.
    assert points["2026-03"]["objects_added"] == 1
    assert points["2026-03"]["objects_total"] == 3


def test_growth_trend_limit_keeps_cumulative_total(tmp_path):
    db = _seed(tmp_path)
    with connect(db) as conn:
        trend = analytics.growth_trend(conn, interval="month", limit=1)

    # Only the most recent period is returned, but its running total still
    # reflects every earlier object.
    assert len(trend["points"]) == 1
    assert trend["points"][0]["period"] == "2026-03"
    assert trend["points"][0]["objects_total"] == 3


def test_growth_trend_ignores_unparseable_timestamps(tmp_path):
    db = str(tmp_path / "catalog.sqlite")
    init_db(db)
    with connect(db) as conn:
        _object(conn, "good", "2026-05-01T00:00:00+00:00")
        _object(conn, "bad", "not-a-timestamp")
        conn.commit()
        trend = analytics.growth_trend(conn, interval="month", limit=12)

    totals = {p["period"]: p["objects_total"] for p in trend["points"]}
    assert totals == {"2026-05": 1}


def test_growth_trend_rejects_unknown_interval(tmp_path):
    db = _seed(tmp_path)
    with connect(db) as conn:
        with pytest.raises(ValueError):
            analytics.growth_trend(conn, interval="decade")
