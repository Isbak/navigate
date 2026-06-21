"""Analytics over the consolidated knowledge graph.

These answer the success-criteria questions directly from knowledge objects
rather than individual documents:

* What are the core capabilities / concepts / technologies?  -> ``top_by_type``
* What is most connected (links many things together)?       -> ``most_connected``
* What is most referenced across the corpus?                 -> ``most_mentioned``
* Where does the evidence disagree?                          -> ``conflicting_evidence``
* What might still be the same thing?                        -> ``duplicate_candidates``

Counts use distinct documents as the primary signal, so something asserted once
in many documents outranks something repeated within a single document.
"""

from __future__ import annotations

import sqlite3

from .resolution import (
    ResolutionConfig,
    cross_type_duplicate_pairs,
    duplicate_candidate_pairs,
)

# Mentions whose confidences straddle this gap signal that documents disagree
# about an object (some assert it strongly, others barely).
_CONFLICT_HIGH = 0.8
_CONFLICT_LOW = 0.4


def _object_rows_with_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT o.id, o.canonical_name, o.object_type, o.confidence, o.status,
               COUNT(DISTINCT m.id) AS mentions,
               COUNT(DISTINCT m.artifact_id) AS documents
        FROM knowledge_objects o
        LEFT JOIN knowledge_mentions m ON m.knowledge_object_id = o.id
        GROUP BY o.id
        """
    ).fetchall()


def top_by_type(
    conn: sqlite3.Connection, object_type: str, limit: int = 10
) -> list[dict]:
    """Top objects of a given type, ranked by documents then confidence."""

    rows = conn.execute(
        """
        SELECT o.id, o.canonical_name, o.confidence, o.status,
               COUNT(DISTINCT m.id) AS mentions,
               COUNT(DISTINCT m.artifact_id) AS documents
        FROM knowledge_objects o
        LEFT JOIN knowledge_mentions m ON m.knowledge_object_id = o.id
        WHERE o.object_type = ?
        GROUP BY o.id
        ORDER BY documents DESC, o.confidence DESC, o.canonical_name
        LIMIT ?
        """,
        (object_type, limit),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["canonical_name"],
            "documents": r["documents"],
            "mentions": r["mentions"],
            "confidence": r["confidence"],
            "status": r["status"],
        }
        for r in rows
    ]


def most_mentioned(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = sorted(
        _object_rows_with_counts(conn),
        key=lambda r: (-r["documents"], -r["mentions"], r["canonical_name"].lower()),
    )
    return [
        {
            "id": r["id"],
            "name": r["canonical_name"],
            "object_type": r["object_type"],
            "documents": r["documents"],
            "mentions": r["mentions"],
            "confidence": r["confidence"],
        }
        for r in rows[:limit]
    ]


def most_connected(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Objects with the most relationships (in + out)."""

    rows = conn.execute(
        """
        SELECT o.id, o.canonical_name, o.object_type, o.confidence,
               (SELECT COUNT(*) FROM knowledge_relationships r
                 WHERE r.source_object = o.id OR r.target_object = o.id) AS degree
        FROM knowledge_objects o
        ORDER BY degree DESC, o.confidence DESC, o.canonical_name
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["canonical_name"],
            "object_type": r["object_type"],
            "degree": r["degree"],
            "confidence": r["confidence"],
        }
        for r in rows
        if r["degree"] > 0
    ]


def conflicting_evidence(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Objects whose mentions span both high- and low-confidence assertions.

    A practical "conflicting evidence" signal: some documents assert the object
    strongly (>= 0.8) while others barely do (<= 0.4), which is worth a human
    look before the object is trusted.
    """

    rows = conn.execute(
        """
        SELECT o.id, o.canonical_name, o.object_type,
               MAX(m.confidence) AS max_conf,
               MIN(m.confidence) AS min_conf,
               COUNT(m.id) AS mentions
        FROM knowledge_objects o
        JOIN knowledge_mentions m ON m.knowledge_object_id = o.id
        GROUP BY o.id
        HAVING MAX(m.confidence) >= ? AND MIN(m.confidence) <= ?
        ORDER BY (MAX(m.confidence) - MIN(m.confidence)) DESC
        LIMIT ?
        """,
        (_CONFLICT_HIGH, _CONFLICT_LOW, limit),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["canonical_name"],
            "object_type": r["object_type"],
            "max_confidence": round(r["max_conf"], 3),
            "min_confidence": round(r["min_conf"], 3),
            "mentions": r["mentions"],
        }
        for r in rows
    ]


# Period bucketing for the growth trend, expressed as SQLite strftime formats.
_GROWTH_INTERVALS = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}

# Each growth series: (table, timestamp column, optional extra WHERE clause).
_GROWTH_SERIES = (
    ("artifacts", "artifacts", "first_seen_at", "scan_status != 'DELETED'"),
    ("objects", "knowledge_objects", "created_at", None),
    ("relationships", "knowledge_relationships", "created_at", None),
)


def growth_trend(
    conn: sqlite3.Connection, *, interval: str = "month", limit: int = 12
) -> dict:
    """Knowledge-growth trend: per-period new and cumulative counts.

    Buckets artifacts, knowledge objects, and relationships by their creation
    timestamp into ``day`` / ``week`` / ``month`` periods, and reports both the
    count added in each period and the running cumulative total. The cumulative
    totals are computed over the full history first, then the most recent
    ``limit`` periods are returned, so the totals stay correct under windowing.
    Rows whose timestamp is missing or unparseable contribute to no period.
    """

    fmt = _GROWTH_INTERVALS.get(interval)
    if fmt is None:
        raise ValueError(
            f"interval must be one of {sorted(_GROWTH_INTERVALS)}, got {interval!r}"
        )

    added: dict[str, dict[str, int]] = {name: {} for name, *_ in _GROWTH_SERIES}
    periods: set[str] = set()
    for name, table, column, where in _GROWTH_SERIES:
        clause = f" WHERE {where}" if where else ""
        rows = conn.execute(
            f"SELECT strftime(?, {column}) AS period, COUNT(*) AS n "
            f"FROM {table}{clause} GROUP BY period",
            (fmt,),
        ).fetchall()
        for r in rows:
            if r["period"] is None:
                continue
            added[name][r["period"]] = r["n"]
            periods.add(r["period"])

    totals = {name: 0 for name, *_ in _GROWTH_SERIES}
    points: list[dict] = []
    for period in sorted(periods):
        for name in totals:
            totals[name] += added[name].get(period, 0)
        points.append(
            {
                "period": period,
                "artifacts_added": added["artifacts"].get(period, 0),
                "artifacts_total": totals["artifacts"],
                "objects_added": added["objects"].get(period, 0),
                "objects_total": totals["objects"],
                "relationships_added": added["relationships"].get(period, 0),
                "relationships_total": totals["relationships"],
            }
        )
    return {"interval": interval, "points": points[-limit:] if limit > 0 else points}


def duplicate_candidates(
    conn: sqlite3.Connection,
    config: ResolutionConfig | None = None,
    limit: int = 20,
) -> list[dict]:
    """Object pairs similar enough to maybe be duplicates, but not auto-merged."""

    objects = [
        (r["id"], r["object_type"], r["canonical_name"])
        for r in conn.execute(
            "SELECT id, object_type, canonical_name FROM knowledge_objects"
        )
    ]
    return duplicate_candidate_pairs(objects, config)[:limit]


def cross_type_duplicates(
    conn: sqlite3.Connection, limit: int = 20
) -> list[dict]:
    """Objects sharing a name across different types - likely the same thing."""

    objects = [
        (r["id"], r["object_type"], r["canonical_name"])
        for r in conn.execute(
            "SELECT id, object_type, canonical_name FROM knowledge_objects"
        )
    ]
    return cross_type_duplicate_pairs(objects)[:limit]


__all__ = [
    "top_by_type",
    "most_mentioned",
    "most_connected",
    "conflicting_evidence",
    "growth_trend",
    "duplicate_candidates",
    "cross_type_duplicates",
]
