"""Persistence for the governance layer.

Owns every SQL statement touching the six governance tables:

* ``knowledge_owners``      - who owns each object
* ``knowledge_lifecycle``   - freshness + review-workflow state per object
* ``knowledge_quality``     - the latest quality score and its factors
* ``knowledge_alerts``      - generated operator alerts
* ``knowledge_change_log``  - the append-only audit trail
* ``knowledge_reviews``     - reused as the human review-action audit trail

It also reads (never writes) the consolidation tables to gather the per-object
metrics governance scores against. Object ids are referenced *softly* (by value)
because consolidation deletes and recreates ``knowledge_objects`` on every run,
and the curated governance state must survive that.
"""

from __future__ import annotations

import sqlite3

# -- object metrics read from the consolidation tables ------------------------


def object_metrics(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per knowledge object: confidence, type, name, document and evidence counts."""

    return conn.execute(
        """
        SELECT o.id, o.canonical_name, o.object_type, o.confidence, o.status,
               COUNT(DISTINCT m.artifact_id) AS document_count,
               COUNT(DISTINCT m.id) AS mention_count,
               (SELECT COUNT(*) FROM knowledge_evidence e
                 WHERE e.knowledge_object_id = o.id) AS evidence_count
        FROM knowledge_objects o
        LEFT JOIN knowledge_mentions m ON m.knowledge_object_id = o.id
        GROUP BY o.id
        """
    ).fetchall()


def relationship_counts(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """Map object_id -> (total_relationships, rejected_relationships).

    Counts every relationship touching the object as either endpoint; a
    relationship is "rejected" when its review status is REJECTED.
    """

    rows = conn.execute(
        """
        SELECT id, source_object, target_object, review_status
        FROM knowledge_relationships
        """
    ).fetchall()
    totals: dict[str, int] = {}
    rejected: dict[str, int] = {}
    for r in rows:
        is_rejected = r["review_status"] == "REJECTED"
        for endpoint in (r["source_object"], r["target_object"]):
            totals[endpoint] = totals.get(endpoint, 0) + 1
            if is_rejected:
                rejected[endpoint] = rejected.get(endpoint, 0) + 1
    return {oid: (totals[oid], rejected.get(oid, 0)) for oid in totals}


def current_relationship_triples(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    """The (source, predicate, target) triples currently in the graph (not rejected)."""

    rows = conn.execute(
        """
        SELECT source_object, predicate, target_object
        FROM knowledge_relationships
        WHERE review_status != 'REJECTED'
        """
    ).fetchall()
    return {(r["source_object"], r["predicate"], r["target_object"]) for r in rows}


# -- ownership ----------------------------------------------------------------


def set_owner(
    conn: sqlite3.Connection,
    *,
    object_id: str,
    owner_type: str,
    owner_id: str,
    assigned_at: str,
    assigned_by: str,
) -> str | None:
    """Assign (or reassign) the owner of an object. Returns the prior owner label."""

    existing = conn.execute(
        "SELECT owner_type, owner_id FROM knowledge_owners WHERE object_id = ?",
        (object_id,),
    ).fetchone()
    prior = f"{existing['owner_type']}:{existing['owner_id']}" if existing else None
    conn.execute(
        """
        INSERT INTO knowledge_owners(object_id, owner_type, owner_id, assigned_at, assigned_by)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(object_id) DO UPDATE SET
            owner_type = excluded.owner_type,
            owner_id = excluded.owner_id,
            assigned_at = excluded.assigned_at,
            assigned_by = excluded.assigned_by
        """,
        (object_id, owner_type, owner_id, assigned_at, assigned_by),
    )
    return prior


def get_owner(conn: sqlite3.Connection, object_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM knowledge_owners WHERE object_id = ?", (object_id,)
    ).fetchone()


def owned_object_ids(conn: sqlite3.Connection) -> set[str]:
    return {
        r["object_id"] for r in conn.execute("SELECT object_id FROM knowledge_owners")
    }


def owner_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        r["object_id"]: r for r in conn.execute("SELECT * FROM knowledge_owners")
    }


def all_owners(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_owners ORDER BY owner_type, owner_id, object_id"
    ).fetchall()


# -- lifecycle ----------------------------------------------------------------


def lifecycle_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        r["object_id"]: r for r in conn.execute("SELECT * FROM knowledge_lifecycle")
    }


def get_lifecycle(conn: sqlite3.Connection, object_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM knowledge_lifecycle WHERE object_id = ?", (object_id,)
    ).fetchone()


def insert_lifecycle(
    conn: sqlite3.Connection,
    *,
    object_id: str,
    name: str,
    object_type: str,
    created_at: str,
    last_seen_at: str,
    last_confidence: float,
    freshness_score: float,
    freshness_state: str,
    review_state: str,
) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_lifecycle(
            object_id, name, object_type, created_at, last_seen_at, last_reviewed_at,
            last_confirmed_at, last_confidence, freshness_score, freshness_state,
            review_state, present, updated_at
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, 1, ?)
        """,
        (
            object_id, name, object_type, created_at, last_seen_at, last_confidence,
            freshness_score, freshness_state, review_state, last_seen_at,
        ),
    )


def update_lifecycle_seen(
    conn: sqlite3.Connection,
    *,
    object_id: str,
    name: str,
    object_type: str,
    last_seen_at: str,
    last_confidence: float,
    freshness_score: float,
    freshness_state: str,
    present: int = 1,
) -> None:
    conn.execute(
        """
        UPDATE knowledge_lifecycle
        SET name = ?, object_type = ?, last_seen_at = ?, last_confidence = ?,
            freshness_score = ?, freshness_state = ?, present = ?, updated_at = ?
        WHERE object_id = ?
        """,
        (
            name, object_type, last_seen_at, last_confidence, freshness_score,
            freshness_state, present, last_seen_at, object_id,
        ),
    )


def update_lifecycle_freshness(
    conn: sqlite3.Connection,
    *,
    object_id: str,
    freshness_score: float,
    freshness_state: str,
    present: int,
    updated_at: str,
) -> None:
    conn.execute(
        """
        UPDATE knowledge_lifecycle
        SET freshness_score = ?, freshness_state = ?, present = ?, updated_at = ?
        WHERE object_id = ?
        """,
        (freshness_score, freshness_state, present, updated_at, object_id),
    )


def set_review_state(
    conn: sqlite3.Connection,
    *,
    object_id: str,
    review_state: str,
    reviewed_at: str,
    confirmed: bool,
) -> bool:
    """Set the governance review state, stamping the review (and confirm) time.

    Creates a lifecycle row if the object has never been scanned, so a reviewer
    can act on an object the moment it exists.
    """

    existing = conn.execute(
        "SELECT object_id FROM knowledge_lifecycle WHERE object_id = ?", (object_id,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO knowledge_lifecycle(
                object_id, created_at, last_seen_at, last_reviewed_at,
                last_confirmed_at, freshness_score, freshness_state,
                review_state, present, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1.0, 'FRESH', ?, 1, ?)
            """,
            (
                object_id, reviewed_at, reviewed_at, reviewed_at,
                reviewed_at if confirmed else None, review_state, reviewed_at,
            ),
        )
        return True
    if confirmed:
        conn.execute(
            """
            UPDATE knowledge_lifecycle
            SET review_state = ?, last_reviewed_at = ?, last_confirmed_at = ?, updated_at = ?
            WHERE object_id = ?
            """,
            (review_state, reviewed_at, reviewed_at, reviewed_at, object_id),
        )
    else:
        conn.execute(
            """
            UPDATE knowledge_lifecycle
            SET review_state = ?, last_reviewed_at = ?, updated_at = ?
            WHERE object_id = ?
            """,
            (review_state, reviewed_at, reviewed_at, object_id),
        )
    return True


def lifecycle_by_freshness(
    conn: sqlite3.Connection, states: tuple[str, ...]
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(states))
    return conn.execute(
        f"SELECT * FROM knowledge_lifecycle WHERE freshness_state IN ({placeholders}) "
        "ORDER BY freshness_score, object_id",
        states,
    ).fetchall()


def lifecycle_by_review(
    conn: sqlite3.Connection, states: tuple[str, ...]
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(states))
    return conn.execute(
        f"SELECT * FROM knowledge_lifecycle WHERE review_state IN ({placeholders}) "
        "AND present = 1 ORDER BY object_id",
        states,
    ).fetchall()


# -- quality ------------------------------------------------------------------


def upsert_quality(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_quality(
            object_id, quality_score, evidence_score, review_score,
            freshness_score, consistency_score, owner_score, confidence_score,
            evidence_count, document_count, computed_at
        ) VALUES (
            :object_id, :quality_score, :evidence_score, :review_score,
            :freshness_score, :consistency_score, :owner_score, :confidence_score,
            :evidence_count, :document_count, :computed_at
        )
        ON CONFLICT(object_id) DO UPDATE SET
            quality_score = excluded.quality_score,
            evidence_score = excluded.evidence_score,
            review_score = excluded.review_score,
            freshness_score = excluded.freshness_score,
            consistency_score = excluded.consistency_score,
            owner_score = excluded.owner_score,
            confidence_score = excluded.confidence_score,
            evidence_count = excluded.evidence_count,
            document_count = excluded.document_count,
            computed_at = excluded.computed_at
        """,
        row,
    )


def quality_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        r["object_id"]: r for r in conn.execute("SELECT * FROM knowledge_quality")
    }


def get_quality(conn: sqlite3.Connection, object_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM knowledge_quality WHERE object_id = ?", (object_id,)
    ).fetchone()


def quality_ranked(conn: sqlite3.Connection, *, ascending: bool = False) -> list[sqlite3.Row]:
    order = "ASC" if ascending else "DESC"
    return conn.execute(
        f"""
        SELECT q.*, o.canonical_name, o.object_type
        FROM knowledge_quality q
        JOIN knowledge_objects o ON o.id = q.object_id
        ORDER BY q.quality_score {order}, q.object_id
        """
    ).fetchall()


def remove_quality_for_absent(conn: sqlite3.Connection) -> None:
    """Drop quality rows for objects that no longer exist in the graph."""

    conn.execute(
        "DELETE FROM knowledge_quality WHERE object_id NOT IN "
        "(SELECT id FROM knowledge_objects)"
    )


def average_quality(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT AVG(quality_score) AS avg FROM knowledge_quality"
    ).fetchone()
    return round(row["avg"], 1) if row and row["avg"] is not None else 0.0


# -- alerts -------------------------------------------------------------------


def clear_open_alerts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM knowledge_alerts WHERE status = 'OPEN'")


def insert_alert(
    conn: sqlite3.Connection,
    *,
    alert_type: str,
    severity: str,
    object_id: str | None,
    message: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_alerts(alert_type, severity, object_id, message, status, created_at)
        VALUES (?, ?, ?, ?, 'OPEN', ?)
        """,
        (alert_type, severity, object_id, message, created_at),
    )


def open_alerts(
    conn: sqlite3.Connection, alert_type: str | None = None
) -> list[sqlite3.Row]:
    if alert_type:
        return conn.execute(
            "SELECT * FROM knowledge_alerts WHERE status = 'OPEN' AND alert_type = ? "
            "ORDER BY severity DESC, id",
            (alert_type,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM knowledge_alerts WHERE status = 'OPEN' ORDER BY severity DESC, id"
    ).fetchall()


def count_open_alerts_by_type(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT alert_type AS key, COUNT(*) AS count FROM knowledge_alerts "
        "WHERE status = 'OPEN' GROUP BY alert_type ORDER BY count DESC, key"
    ).fetchall()


# -- change log ---------------------------------------------------------------


def insert_change(
    conn: sqlite3.Connection,
    *,
    change_type: str,
    target_kind: str,
    object_id: str | None,
    field: str = "",
    old_value: str = "",
    new_value: str = "",
    detail: str = "",
    detected_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_change_log(
            change_type, target_kind, object_id, field, old_value, new_value, detail, detected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (change_type, target_kind, object_id, field, old_value, new_value, detail, detected_at),
    )


def changes_for_object(conn: sqlite3.Connection, object_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_change_log WHERE object_id = ? ORDER BY id",
        (object_id,),
    ).fetchall()


def recent_changes(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_change_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def all_changes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_change_log ORDER BY id"
    ).fetchall()


def change_feed(
    conn: sqlite3.Connection,
    *,
    object_id: str | None = None,
    change_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    """Filtered, paginated change-log feed (newest first) with a total count."""

    where: list[str] = []
    params: list[object] = []
    if object_id:
        where.append("object_id = ?")
        params.append(object_id)
    if change_type:
        where.append("change_type = ?")
        params.append(change_type)
    clause = f" WHERE {' AND '.join(where)}" if where else ""

    total = int(
        conn.execute(
            f"SELECT COUNT(*) FROM knowledge_change_log{clause}", params
        ).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT * FROM knowledge_change_log{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return rows, total


def known_relationship_triples(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    """Replay the change log to reconstruct the last-known set of relationships.

    Each relationship_added/removed entry stores its triple as
    ``"source|predicate|target"`` in the ``detail`` column; replaying them in
    order yields the set governance believed was live as of the previous scan.
    """

    live: set[tuple[str, str, str]] = set()
    rows = conn.execute(
        "SELECT change_type, detail FROM knowledge_change_log "
        "WHERE change_type IN ('relationship_added', 'relationship_removed') ORDER BY id"
    ).fetchall()
    for r in rows:
        parts = (r["detail"] or "").split("|")
        if len(parts) != 3:
            continue
        triple = (parts[0], parts[1], parts[2])
        if r["change_type"] == "relationship_added":
            live.add(triple)
        else:
            live.discard(triple)
    return live


__all__ = [
    "object_metrics",
    "relationship_counts",
    "current_relationship_triples",
    "set_owner",
    "get_owner",
    "owned_object_ids",
    "owner_map",
    "all_owners",
    "lifecycle_map",
    "get_lifecycle",
    "insert_lifecycle",
    "update_lifecycle_seen",
    "update_lifecycle_freshness",
    "set_review_state",
    "lifecycle_by_freshness",
    "lifecycle_by_review",
    "upsert_quality",
    "quality_map",
    "get_quality",
    "quality_ranked",
    "remove_quality_for_absent",
    "average_quality",
    "clear_open_alerts",
    "insert_alert",
    "open_alerts",
    "count_open_alerts_by_type",
    "insert_change",
    "changes_for_object",
    "recent_changes",
    "all_changes",
    "change_feed",
    "known_relationship_triples",
]
