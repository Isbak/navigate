"""Orphan detection.

Orphans are the loose ends a governed graph should not have. This module finds
the five kinds the spec names, reading the SQLite system of record directly:

* objects with no evidence            - untraceable claims
* objects with no relationships       - islands disconnected from the graph
* objects with no owner               - no one accountable
* relationships with no evidence      - links asserted without a source
* evidence with no object             - dangling provenance

Each check returns plain dicts so the CLI, alerts, and exports share one source
of truth.
"""

from __future__ import annotations

import json
import sqlite3

from . import repository as repo


def _has_evidence_payload(raw: object) -> bool:
    if not raw:
        return False
    try:
        return bool(json.loads(raw))
    except (TypeError, ValueError):
        return bool(str(raw).strip())


def objects_without_evidence(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT o.id, o.canonical_name, o.object_type
        FROM knowledge_objects o
        WHERE NOT EXISTS (
            SELECT 1 FROM knowledge_evidence e WHERE e.knowledge_object_id = o.id
        )
        ORDER BY o.id
        """
    ).fetchall()
    return [
        {"id": r["id"], "name": r["canonical_name"], "type": r["object_type"]}
        for r in rows
    ]


def objects_without_relationships(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT o.id, o.canonical_name, o.object_type
        FROM knowledge_objects o
        WHERE NOT EXISTS (
            SELECT 1 FROM knowledge_relationships r
            WHERE (r.source_object = o.id OR r.target_object = o.id)
              AND r.review_status != 'REJECTED'
        )
        ORDER BY o.id
        """
    ).fetchall()
    return [
        {"id": r["id"], "name": r["canonical_name"], "type": r["object_type"]}
        for r in rows
    ]


def objects_without_owner(conn: sqlite3.Connection) -> list[dict]:
    owned = repo.owned_object_ids(conn)
    rows = conn.execute(
        "SELECT id, canonical_name, object_type FROM knowledge_objects ORDER BY id"
    ).fetchall()
    return [
        {"id": r["id"], "name": r["canonical_name"], "type": r["object_type"]}
        for r in rows
        if r["id"] not in owned
    ]


def relationships_without_evidence(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, source_object, predicate, target_object, evidence
        FROM knowledge_relationships
        WHERE review_status != 'REJECTED'
        ORDER BY id
        """
    ).fetchall()
    return [
        {
            "id": r["id"],
            "source": r["source_object"],
            "predicate": r["predicate"],
            "target": r["target_object"],
        }
        for r in rows
        if not _has_evidence_payload(r["evidence"])
    ]


def evidence_without_object(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT e.id, e.knowledge_object_id, e.artifact_id
        FROM knowledge_evidence e
        WHERE e.knowledge_object_id NOT IN (SELECT id FROM knowledge_objects)
        ORDER BY e.id
        """
    ).fetchall()
    return [
        {"id": r["id"], "object_id": r["knowledge_object_id"], "artifact": r["artifact_id"]}
        for r in rows
    ]


def all_orphans(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Run every orphan check and return them in one report dict."""

    return {
        "objects_without_evidence": objects_without_evidence(conn),
        "objects_without_relationships": objects_without_relationships(conn),
        "objects_without_owner": objects_without_owner(conn),
        "relationships_without_evidence": relationships_without_evidence(conn),
        "evidence_without_object": evidence_without_object(conn),
    }


__all__ = [
    "objects_without_evidence",
    "objects_without_relationships",
    "objects_without_owner",
    "relationships_without_evidence",
    "evidence_without_object",
    "all_orphans",
]
