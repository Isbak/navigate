"""Read-side persistence for the REST API.

This module owns the filtered, paginated read queries the API needs that do not
already exist in the domain repositories (artifact listing, knowledge-object
listing with governance/owner joins, relationship and evidence listing). It
reuses the existing domain repositories wherever they already provide what is
needed; it never writes, and it keeps all SQL out of the route handlers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Columns selected for an artifact; ``id`` is content-addressed (duplicates share
# it) so a detail lookup returns a single representative row.
_ARTIFACT_COLUMNS = (
    "path, id, filename, file_type, size_bytes, created_at, modified_at, sha256, "
    "source_system, scan_status, first_seen_at, last_scanned_at"
)


# -- artifacts ----------------------------------------------------------------

def extracted_artifact_ids(cache_dir: str | Path) -> set[str]:
    """Artifact ids that have an extracted-text cache entry on disk."""

    cache = Path(cache_dir)
    if not cache.exists():
        return set()
    return {
        child.name
        for child in cache.iterdir()
        if child.is_dir() and (child / "extracted.txt").exists()
    }


def classified_artifact_ids(conn: sqlite3.Connection) -> set[str]:
    """Artifact ids that have a semantic classification."""

    return {
        r["artifact_id"]
        for r in conn.execute("SELECT artifact_id FROM document_classifications")
    }


def list_artifacts(
    conn: sqlite3.Connection,
    *,
    file_type: str | None = None,
    scan_status: str | None = None,
    classification_status: str | None = None,
    extraction_status: str | None = None,
    search: str | None = None,
    extracted_ids: set[str] | None = None,
    classified_ids: set[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    """List indexed artifacts with filtering and pagination.

    ``extraction_status`` is derived from the on-disk cache (the set of
    ``extracted_ids``) and ``classification_status`` from the semantic layer, so
    filtering on them is applied as an ``id IN (...)`` constraint to keep the
    total count and pagination correct.
    """

    where = ["scan_status != 'DELETED'"]
    params: list[object] = []
    if file_type:
        where.append("file_type = ?")
        params.append(file_type)
    if scan_status:
        where.append("scan_status = ?")
        params.append(scan_status)
    if search:
        where.append("(filename LIKE ? COLLATE NOCASE OR path LIKE ? COLLATE NOCASE)")
        like = f"%{search.strip()}%"
        params.extend([like, like])

    if classification_status:
        ids = classified_ids if classified_ids is not None else classified_artifact_ids(conn)
        _apply_id_filter(where, params, ids, classification_status, "CLASSIFIED", "UNCLASSIFIED")

    if extraction_status:
        ids = extracted_ids if extracted_ids is not None else extracted_artifact_ids(Path("cache"))
        _apply_id_filter(where, params, ids, extraction_status, "EXTRACTED", "PENDING")

    clause = " WHERE " + " AND ".join(where)
    total = int(
        conn.execute(f"SELECT COUNT(*) FROM artifacts{clause}", params).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT {_ARTIFACT_COLUMNS} FROM artifacts{clause} "
        "ORDER BY filename, path LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return rows, total


def _apply_id_filter(
    where: list[str],
    params: list[object],
    ids: set[str],
    requested: str,
    positive_label: str,
    negative_label: str,
) -> None:
    """Constrain on membership of ``ids`` based on a requested status label."""

    wants_positive = requested.strip().upper() == positive_label
    if not ids:
        # Empty set: positive request matches nothing, negative matches everything.
        where.append("1=1" if not wants_positive else "1=0")
        return
    placeholders = ",".join("?" * len(ids))
    op = "IN" if wants_positive else "NOT IN"
    where.append(f"id {op} ({placeholders})")
    params.extend(sorted(ids))


def get_artifact(conn: sqlite3.Connection, artifact_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_ARTIFACT_COLUMNS} FROM artifacts WHERE id = ? "
        "AND scan_status != 'DELETED' LIMIT 1",
        (artifact_id,),
    ).fetchone()


def evidence_for_artifact(conn: sqlite3.Connection, artifact_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_evidence WHERE artifact_id = ? ORDER BY id",
        (artifact_id,),
    ).fetchall()


# -- links --------------------------------------------------------------------

def list_links(
    conn: sqlite3.Connection,
    *,
    source_artifact_id: str | None = None,
    target_system: str | None = None,
    target_type: str | None = None,
    link_kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    where: list[str] = []
    params: list[object] = []
    for column, value in (
        ("source_artifact_id", source_artifact_id),
        ("target_system", target_system),
        ("target_type", target_type),
        ("link_kind", link_kind),
        ("status", status),
    ):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    clause = f" WHERE {' AND '.join(where)}" if where else ""

    total = int(
        conn.execute(f"SELECT COUNT(*) FROM links{clause}", params).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT * FROM links{clause} ORDER BY source_artifact_id, normalized_url "
        "LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return rows, total


# -- knowledge objects --------------------------------------------------------

def list_knowledge_objects(
    conn: sqlite3.Connection,
    *,
    object_type: str | None = None,
    status: str | None = None,
    review_status: str | None = None,
    owner: str | None = None,
    domain: str | None = None,
    min_confidence: float | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    """List knowledge objects, joining governance (review/freshness/quality) and
    ownership so the API can filter and return a rich object in one query."""

    where: list[str] = []
    params: list[object] = []
    if object_type:
        where.append("o.object_type = ?")
        params.append(object_type)
    if status:
        where.append("o.status = ?")
        params.append(status)
    if review_status:
        where.append("l.review_state = ?")
        params.append(review_status)
    if owner:
        where.append("(w.owner_id = ? OR w.owner_id LIKE ? COLLATE NOCASE)")
        params.extend([owner, f"%{owner}%"])
    if min_confidence is not None:
        where.append("o.confidence >= ?")
        params.append(min_confidence)
    if search:
        like = f"%{search.strip()}%"
        where.append(
            "(o.name LIKE ? COLLATE NOCASE OR o.canonical_name LIKE ? COLLATE NOCASE "
            "OR o.description LIKE ? COLLATE NOCASE)"
        )
        params.extend([like, like, like])
    if domain:
        # An object is in a domain if any document that mentions it was classified
        # into that domain. ``domains`` is a JSON blob; a LIKE match is sufficient.
        where.append(
            "EXISTS (SELECT 1 FROM knowledge_mentions km "
            "JOIN document_classifications dc ON dc.artifact_id = km.artifact_id "
            "WHERE km.knowledge_object_id = o.id AND dc.domains LIKE ? COLLATE NOCASE)"
        )
        params.append(f"%{domain}%")

    clause = f" WHERE {' AND '.join(where)}" if where else ""
    base = (
        "FROM knowledge_objects o "
        "LEFT JOIN knowledge_lifecycle l ON l.object_id = o.id "
        "LEFT JOIN knowledge_quality q ON q.object_id = o.id "
        "LEFT JOIN knowledge_owners w ON w.object_id = o.id"
    )
    total = int(
        conn.execute(f"SELECT COUNT(*) {base}{clause}", params).fetchone()[0]
    )
    rows = conn.execute(
        "SELECT o.*, l.review_state AS review_state, l.freshness_state AS freshness_state, "
        "q.quality_score AS quality_score, "
        "w.owner_type AS owner_type, w.owner_id AS owner_id "
        f"{base}{clause} ORDER BY o.confidence DESC, o.canonical_name LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return rows, total


def get_knowledge_object(conn: sqlite3.Connection, object_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT o.*, l.review_state AS review_state, l.freshness_state AS freshness_state, "
        "q.quality_score AS quality_score, "
        "w.owner_type AS owner_type, w.owner_id AS owner_id "
        "FROM knowledge_objects o "
        "LEFT JOIN knowledge_lifecycle l ON l.object_id = o.id "
        "LEFT JOIN knowledge_quality q ON q.object_id = o.id "
        "LEFT JOIN knowledge_owners w ON w.object_id = o.id "
        "WHERE o.id = ?",
        (object_id,),
    ).fetchone()


# -- relationships ------------------------------------------------------------

def list_relationships(
    conn: sqlite3.Connection,
    *,
    source_object_id: str | None = None,
    target_object_id: str | None = None,
    predicate: str | None = None,
    review_status: str | None = None,
    min_confidence: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    where: list[str] = []
    params: list[object] = []
    if source_object_id:
        where.append("source_object = ?")
        params.append(source_object_id)
    if target_object_id:
        where.append("target_object = ?")
        params.append(target_object_id)
    if predicate:
        where.append("predicate = ?")
        params.append(predicate)
    if review_status:
        where.append("review_status = ?")
        params.append(review_status)
    if min_confidence is not None:
        where.append("confidence >= ?")
        params.append(min_confidence)
    clause = f" WHERE {' AND '.join(where)}" if where else ""

    total = int(
        conn.execute(
            f"SELECT COUNT(*) FROM knowledge_relationships{clause}", params
        ).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT * FROM knowledge_relationships{clause} "
        "ORDER BY confidence DESC, id LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return rows, total


def get_relationship(conn: sqlite3.Connection, relationship_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM knowledge_relationships WHERE id = ?", (relationship_id,)
    ).fetchone()


# -- evidence -----------------------------------------------------------------

def list_evidence(
    conn: sqlite3.Connection,
    *,
    artifact_id: str | None = None,
    knowledge_object_id: str | None = None,
    relationship_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    """List evidence rows.

    ``relationship_id`` has no direct column on ``knowledge_evidence`` (a
    relationship stores its quotes inline), so it is interpreted as "evidence for
    either endpoint object of that relationship".
    """

    where: list[str] = []
    params: list[object] = []
    if artifact_id:
        where.append("artifact_id = ?")
        params.append(artifact_id)
    if knowledge_object_id:
        where.append("knowledge_object_id = ?")
        params.append(knowledge_object_id)
    if relationship_id is not None:
        rel = get_relationship(conn, relationship_id)
        endpoints = (
            [rel["source_object"], rel["target_object"]] if rel is not None else ["\0"]
        )
        placeholders = ",".join("?" * len(endpoints))
        where.append(f"knowledge_object_id IN ({placeholders})")
        params.extend(endpoints)
    clause = f" WHERE {' AND '.join(where)}" if where else ""

    total = int(
        conn.execute(
            f"SELECT COUNT(*) FROM knowledge_evidence{clause}", params
        ).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT * FROM knowledge_evidence{clause} ORDER BY id LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return rows, total


def get_evidence(conn: sqlite3.Connection, evidence_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM knowledge_evidence WHERE id = ?", (evidence_id,)
    ).fetchone()


__all__ = [
    "extracted_artifact_ids",
    "classified_artifact_ids",
    "list_artifacts",
    "get_artifact",
    "evidence_for_artifact",
    "list_links",
    "list_knowledge_objects",
    "get_knowledge_object",
    "list_relationships",
    "get_relationship",
    "list_evidence",
    "get_evidence",
]
