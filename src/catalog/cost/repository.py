"""Persistence for the LLM usage ledger.

Owns every SQL statement that touches ``llm_usage``: one insert per provider
call and the aggregate read queries the cost report renders. Like the semantic
tables it references the content-addressed ``artifact_id`` softly (by value, with
an index) rather than via an enforced foreign key.
"""

from __future__ import annotations

import sqlite3


def record_usage(
    conn: sqlite3.Connection,
    *,
    operation: str,
    model: str,
    provider: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    latency_ms: float | None = None,
    cost_usd: float | None = None,
    artifact_id: str | None = None,
    run_id: int | None = None,
    created_at: str,
) -> int:
    """Insert one priced call. ``total_tokens`` is derived from input+output."""

    cur = conn.execute(
        """
        INSERT INTO llm_usage(
            operation, artifact_id, model, provider,
            input_tokens, output_tokens, total_tokens,
            cache_read_tokens, cache_write_tokens, latency_ms,
            cost_usd, run_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            operation,
            artifact_id,
            model,
            provider,
            input_tokens,
            output_tokens,
            input_tokens + output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            latency_ms,
            cost_usd,
            run_id,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def totals(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute(
        """
        SELECT
          COUNT(*) AS calls,
          COALESCE(SUM(input_tokens), 0) AS input_tokens,
          COALESCE(SUM(output_tokens), 0) AS output_tokens,
          COALESCE(SUM(total_tokens), 0) AS total_tokens,
          COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
          COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
          SUM(cost_usd) AS cost_usd,
          SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_calls
        FROM llm_usage
        """
    ).fetchone()


def by_operation(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
          operation AS key,
          COUNT(*) AS calls,
          COALESCE(SUM(total_tokens), 0) AS total_tokens,
          SUM(cost_usd) AS cost_usd
        FROM llm_usage
        GROUP BY operation
        ORDER BY cost_usd IS NULL, cost_usd DESC, total_tokens DESC, key
        """
    ).fetchall()


def by_model(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
          model AS key,
          COUNT(*) AS calls,
          COALESCE(SUM(total_tokens), 0) AS total_tokens,
          SUM(cost_usd) AS cost_usd,
          SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_calls
        FROM llm_usage
        GROUP BY model
        ORDER BY cost_usd IS NULL, cost_usd DESC, total_tokens DESC, key
        """
    ).fetchall()


def cost_per_document(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    """Per-artifact spend, highest first. Covers every operation for the doc."""

    sql = """
        SELECT
          artifact_id AS key,
          COUNT(*) AS calls,
          COALESCE(SUM(total_tokens), 0) AS total_tokens,
          SUM(cost_usd) AS cost_usd
        FROM llm_usage
        WHERE artifact_id IS NOT NULL
        GROUP BY artifact_id
        ORDER BY cost_usd IS NULL, cost_usd DESC, total_tokens DESC, key
    """
    if limit is not None:
        sql += " LIMIT ?"
        return conn.execute(sql, (limit,)).fetchall()
    return conn.execute(sql).fetchall()


def cost_vs_quality(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    """Per-document spend beside the model's own classification confidence.

    A LEFT JOIN keeps documents that have usage but no classification yet
    (``document_type``/``type_confidence`` come back NULL for those).
    """

    sql = """
        SELECT
          u.artifact_id AS key,
          dc.document_type AS document_type,
          dc.type_confidence AS type_confidence,
          COUNT(*) AS calls,
          COALESCE(SUM(u.total_tokens), 0) AS total_tokens,
          SUM(u.cost_usd) AS cost_usd
        FROM llm_usage u
        LEFT JOIN document_classifications dc
          ON dc.artifact_id = u.artifact_id
        WHERE u.artifact_id IS NOT NULL
        GROUP BY u.artifact_id, dc.document_type, dc.type_confidence
        ORDER BY cost_usd IS NULL, cost_usd DESC, total_tokens DESC, key
    """
    if limit is not None:
        sql += " LIMIT ?"
        return conn.execute(sql, (limit,)).fetchall()
    return conn.execute(sql).fetchall()


__all__ = [
    "record_usage",
    "totals",
    "by_operation",
    "by_model",
    "cost_per_document",
    "cost_vs_quality",
]
