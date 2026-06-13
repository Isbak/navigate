"""Persistence for discovered links and link-scan runs.

This layer owns all SQL touching the ``links`` and ``link_scan_runs`` tables.
It deliberately knows nothing about cache files or classification rules - the
service layer feeds it already-normalized, already-classified records.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

ACTIVE = "ACTIVE"
STALE = "STALE"


@dataclass
class UpsertResult:
    """Outcome of persisting a single link."""

    is_new: bool
    link_id: int


def upsert_link(
    conn: sqlite3.Connection,
    *,
    source_artifact_id: str,
    raw_url: str,
    normalized_url: str,
    anchor_text: str | None,
    target_system: str,
    target_type: str,
    link_kind: str,
    seen_at: str,
) -> UpsertResult:
    """Insert a new link or refresh an existing one.

    Deduplication key: ``source_artifact_id + normalized_url + anchor_text``.
    An existing match has its ``last_seen_at`` refreshed, its classification
    updated, and its status reset to ACTIVE (a previously-stale link that
    reappears is active again). The original ``discovered_at`` is preserved.
    """

    row = conn.execute(
        """
        SELECT id FROM links
        WHERE source_artifact_id = ?
          AND normalized_url = ?
          AND COALESCE(anchor_text, '') = COALESCE(?, '')
        """,
        (source_artifact_id, normalized_url, anchor_text),
    ).fetchone()

    if row is not None:
        link_id = int(row["id"])
        conn.execute(
            """
            UPDATE links
            SET raw_url = ?, target_system = ?, target_type = ?, link_kind = ?,
                last_seen_at = ?, status = ?
            WHERE id = ?
            """,
            (
                raw_url,
                target_system,
                target_type,
                link_kind,
                seen_at,
                ACTIVE,
                link_id,
            ),
        )
        return UpsertResult(is_new=False, link_id=link_id)

    cur = conn.execute(
        """
        INSERT INTO links(
            source_artifact_id, raw_url, normalized_url, anchor_text,
            target_system, target_type, link_kind,
            discovered_at, last_seen_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_artifact_id,
            raw_url,
            normalized_url,
            anchor_text,
            target_system,
            target_type,
            link_kind,
            seen_at,
            seen_at,
            ACTIVE,
        ),
    )
    return UpsertResult(is_new=True, link_id=int(cur.lastrowid))


def mark_stale_for_artifact(
    conn: sqlite3.Connection, source_artifact_id: str, seen_at: str
) -> int:
    """Mark an artifact's previously-active links not seen in this run as STALE.

    Links are never physically deleted; a link whose ``last_seen_at`` predates
    the current run timestamp was absent from the latest extraction and is
    flagged STALE so it can still be reported.
    """

    cur = conn.execute(
        """
        UPDATE links
        SET status = ?
        WHERE source_artifact_id = ?
          AND status = ?
          AND last_seen_at < ?
        """,
        (STALE, source_artifact_id, ACTIVE, seen_at),
    )
    return cur.rowcount


def record_link_scan_run(
    conn: sqlite3.Connection,
    *,
    started_at: str,
    completed_at: str,
    artifacts_processed: int,
    links_found: int,
    links_new: int,
    links_updated: int,
    links_removed: int,
    errors: int,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO link_scan_runs(
            started_at, completed_at, artifacts_processed,
            links_found, links_new, links_updated, links_removed, errors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            started_at,
            completed_at,
            artifacts_processed,
            links_found,
            links_new,
            links_updated,
            links_removed,
            errors,
        ),
    )
    return int(cur.lastrowid)


def latest_link_scan_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM link_scan_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()


# -- read helpers used by reporting / CLI --------------------------------------

def count_links(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM links").fetchone()[0])


def counts_by(conn: sqlite3.Connection, column: str) -> list[sqlite3.Row]:
    if column not in {"target_system", "target_type", "link_kind", "status"}:
        raise ValueError(f"Unsupported grouping column: {column}")
    return conn.execute(
        f"SELECT {column} AS key, COUNT(*) AS count "
        f"FROM links GROUP BY {column} ORDER BY count DESC, key"
    ).fetchall()


def top_referenced_urls(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT normalized_url AS key, COUNT(*) AS count "
        "FROM links GROUP BY normalized_url ORDER BY count DESC, key LIMIT ?",
        (limit,),
    ).fetchall()


def top_linking_artifacts(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT source_artifact_id AS key, COUNT(*) AS count "
        "FROM links GROUP BY source_artifact_id ORDER BY count DESC, key LIMIT ?",
        (limit,),
    ).fetchall()


def links_for_artifact(conn: sqlite3.Connection, artifact_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM links WHERE source_artifact_id = ? ORDER BY normalized_url",
        (artifact_id,),
    ).fetchall()


def links_for_system(conn: sqlite3.Connection, system: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM links WHERE target_system = ? ORDER BY source_artifact_id, normalized_url",
        (system,),
    ).fetchall()


def stale_links(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM links WHERE status = ? ORDER BY source_artifact_id, normalized_url",
        (STALE,),
    ).fetchall()


def all_links(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM links ORDER BY source_artifact_id, normalized_url"
    ).fetchall()


__all__ = [
    "ACTIVE",
    "STALE",
    "UpsertResult",
    "upsert_link",
    "mark_stale_for_artifact",
    "record_link_scan_run",
    "latest_link_scan_run",
    "count_links",
    "counts_by",
    "top_referenced_urls",
    "top_linking_artifacts",
    "links_for_artifact",
    "links_for_system",
    "stale_links",
    "all_links",
]
