"""Persistence for the knowledge layer.

Owns every SQL statement touching the five knowledge tables:

* ``knowledge_objects``        - the consolidated, reusable objects
* ``knowledge_mentions``       - every (object, document) occurrence
* ``knowledge_evidence``       - traceable quotes; no object exists without one
* ``knowledge_relationships``  - typed links between objects
* ``knowledge_reviews``        - the audit trail of review actions

It also reads the upstream semantic ``candidate_*`` tables to gather the raw
mentions consolidation starts from. The service layer hands this module
already-resolved records; nothing here does resolution, scoring, or prompting.
"""

from __future__ import annotations

import sqlite3

from .ids import equation_display_name, requirement_display_name
from .models import RawMention, ReviewState

# (semantic table, object_type, name expression) tuples that feed consolidation.
# The name expression is the cluster key: for decisions/risks it is the short
# ``title`` (falling back to the full sentence for rows that predate it), so the
# same decision/risk worded differently across documents collapses into one
# object instead of one node per sentence. candidate_entities is handled
# separately because its type lives in a column.
_CANDIDATE_SOURCES = (
    ("candidate_capabilities", "Capability", "name"),
    ("candidate_decisions", "Decision",
     "COALESCE(NULLIF(TRIM(title), ''), decision_text)"),
    ("candidate_risks", "Risk",
     "COALESCE(NULLIF(TRIM(title), ''), risk_description)"),
)


def _in_scope(artifact_id: str, allowed: set[str] | None) -> bool:
    """True when ``artifact_id`` may be consolidated.

    ``allowed is None`` disables scoping entirely (full backward compatibility);
    otherwise only ids in the set - the in-scope file-backed and curated ids
    computed by :mod:`catalog.knowledge.scope` - pass.
    """

    return allowed is None or artifact_id in allowed


def gather_mentions(
    conn: sqlite3.Connection,
    min_confidence: float = 0.0,
    allowed_artifact_ids: set[str] | None = None,
) -> list[RawMention]:
    """Read every entity proposal from the semantic layer as a flat list.

    Pulls from ``candidate_capabilities`` / ``candidate_decisions`` /
    ``candidate_risks`` (fixed type per table) and ``candidate_entities`` (type
    in a column). REJECTED proposals are skipped; everything else - including the
    default NEW - is fair game for consolidation.

    When ``allowed_artifact_ids`` is given, mentions from any other artifact are
    dropped so only documents under a configured source folder (plus curated
    imports) are consolidated.
    """

    out: list[RawMention] = []

    for table, object_type, name_col in _CANDIDATE_SOURCES:
        rows = conn.execute(
            f"""
            SELECT artifact_id, {name_col} AS name, confidence, supporting_text
            FROM {table}
            WHERE confidence >= ? AND review_status != ?
              AND {name_col} IS NOT NULL AND TRIM({name_col}) != ''
            """,
            (min_confidence, ReviewState.REJECTED.value),
        ).fetchall()
        out.extend(
            RawMention(
                object_type=object_type,
                name=r["name"],
                artifact_id=r["artifact_id"],
                confidence=r["confidence"] if r["confidence"] is not None else 0.0,
                source_text=r["supporting_text"] or "",
            )
            for r in rows
            if _in_scope(r["artifact_id"], allowed_artifact_ids)
        )

    entity_rows = conn.execute(
        """
        SELECT artifact_id, entity_type, name, confidence, supporting_text
        FROM candidate_entities
        WHERE confidence >= ? AND review_status != ?
          AND name IS NOT NULL AND TRIM(name) != ''
          AND entity_type IS NOT NULL AND TRIM(entity_type) != ''
        """,
        (min_confidence, ReviewState.REJECTED.value),
    ).fetchall()
    out.extend(
        RawMention(
            object_type=r["entity_type"],
            name=r["name"],
            artifact_id=r["artifact_id"],
            confidence=r["confidence"] if r["confidence"] is not None else 0.0,
            source_text=r["supporting_text"] or "",
        )
        for r in entity_rows
        if _in_scope(r["artifact_id"], allowed_artifact_ids)
    )

    # Compliance: each candidate requirement yields a Requirement mention (named
    # by its standard + clause locator) and, when a standard is named, a Standard
    # mention - so both become first-class knowledge objects the rest of the
    # pipeline scores, governs, and projects to RDF like any other.
    for r in gather_candidate_requirements(conn, min_confidence, allowed_artifact_ids):
        conf = r["confidence"] if r["confidence"] is not None else 0.0
        evidence = (r["supporting_text"] or r["requirement_text"] or "").strip()
        req_name = requirement_display_name(
            r["standard_name"] or "", r["clause_ref"] or "", r["title"] or ""
        )
        out.append(
            RawMention(
                object_type="Requirement",
                name=req_name,
                artifact_id=r["artifact_id"],
                confidence=conf,
                source_text=evidence or req_name,
            )
        )
        standard_name = (r["standard_name"] or "").strip()
        if standard_name:
            out.append(
                RawMention(
                    object_type="Standard",
                    name=standard_name,
                    artifact_id=r["artifact_id"],
                    confidence=conf,
                    source_text=(r["standard_version"] and f"{standard_name} {r['standard_version']}") or standard_name,
                )
            )

    # Compliance: each candidate equation yields an Equation mention (named by its
    # standard + result symbol) and, when a standard is named, a Standard mention -
    # so a formula-only standard still gets a first-class Standard object too.
    for r in gather_candidate_equations(conn, min_confidence, allowed_artifact_ids):
        conf = r["confidence"] if r["confidence"] is not None else 0.0
        eq_name = equation_display_name(
            r["standard_name"] or "", r["symbol"] or "", r["clause_ref"] or ""
        )
        evidence = (
            r["supporting_text"] or r["expression"] or r["title"] or eq_name
        ).strip()
        out.append(
            RawMention(
                object_type="Equation",
                name=eq_name,
                artifact_id=r["artifact_id"],
                confidence=conf,
                source_text=evidence or eq_name,
            )
        )
        standard_name = (r["standard_name"] or "").strip()
        if standard_name:
            out.append(
                RawMention(
                    object_type="Standard",
                    name=standard_name,
                    artifact_id=r["artifact_id"],
                    confidence=conf,
                    source_text=(r["standard_version"] and f"{standard_name} {r['standard_version']}") or standard_name,
                )
            )
    return out


def gather_candidate_equations(
    conn: sqlite3.Connection,
    min_confidence: float = 0.0,
    allowed_artifact_ids: set[str] | None = None,
) -> list[sqlite3.Row]:
    """Read candidate equation rows (LLM-mined or curated import).

    REJECTED proposals are skipped; a row needs at least a symbol, an expression,
    or a clause ref to be usable. ``allowed_artifact_ids`` restricts the result to
    in-scope artifacts (see :func:`gather_mentions`).
    """

    rows = conn.execute(
        """
        SELECT artifact_id, standard_name, standard_version, clause_ref, symbol,
               title, expression, python_code, ast_json, variables, latex, valid,
               validation_note, confidence, supporting_text
        FROM candidate_equations
        WHERE confidence >= ? AND review_status != ?
          AND (
            (symbol IS NOT NULL AND TRIM(symbol) != '')
            OR (expression IS NOT NULL AND TRIM(expression) != '')
            OR (clause_ref IS NOT NULL AND TRIM(clause_ref) != '')
          )
        """,
        (min_confidence, ReviewState.REJECTED.value),
    ).fetchall()
    return [r for r in rows if _in_scope(r["artifact_id"], allowed_artifact_ids)]


def gather_candidate_requirements(
    conn: sqlite3.Connection,
    min_confidence: float = 0.0,
    allowed_artifact_ids: set[str] | None = None,
) -> list[sqlite3.Row]:
    """Read candidate requirement clauses (LLM-mined or curated import).

    REJECTED proposals are skipped; a row needs at least a clause ref or some
    requirement text to be usable. ``allowed_artifact_ids`` restricts the result
    to in-scope artifacts (see :func:`gather_mentions`).
    """

    rows = conn.execute(
        """
        SELECT artifact_id, standard_name, standard_version, clause_ref, title,
               requirement_text, obligation_level, confidence, supporting_text
        FROM candidate_requirements
        WHERE confidence >= ? AND review_status != ?
          AND (
            (clause_ref IS NOT NULL AND TRIM(clause_ref) != '')
            OR (requirement_text IS NOT NULL AND TRIM(requirement_text) != '')
            OR (title IS NOT NULL AND TRIM(title) != '')
          )
        """,
        (min_confidence, ReviewState.REJECTED.value),
    ).fetchall()
    return [r for r in rows if _in_scope(r["artifact_id"], allowed_artifact_ids)]


def gather_candidate_relationships(
    conn: sqlite3.Connection,
    min_confidence: float = 0.0,
    allowed_artifact_ids: set[str] | None = None,
) -> list[sqlite3.Row]:
    """Read per-document relationship proposals from the semantic layer.

    These have free-text ``subject``/``object`` names that the service resolves
    against the consolidated objects before persisting a knowledge relationship.
    ``allowed_artifact_ids`` restricts the result to in-scope artifacts (see
    :func:`gather_mentions`).
    """

    rows = conn.execute(
        """
        SELECT artifact_id, subject, predicate, object, confidence, supporting_text
        FROM candidate_relationships
        WHERE confidence >= ? AND review_status != ?
          AND subject IS NOT NULL AND TRIM(subject) != ''
          AND object IS NOT NULL AND TRIM(object) != ''
          AND predicate IS NOT NULL AND TRIM(predicate) != ''
        """,
        (min_confidence, ReviewState.REJECTED.value),
    ).fetchall()
    return [r for r in rows if _in_scope(r["artifact_id"], allowed_artifact_ids)]


# -- write side ---------------------------------------------------------------

def clear_consolidated(conn: sqlite3.Connection) -> None:
    """Remove all derived rows, keeping the human review audit trail.

    Used by a normal ``consolidate``: objects/mentions/evidence/relationships are
    fully rebuilt from current semantic data, but ``knowledge_reviews`` survive so
    a re-run can re-apply prior approvals to objects with the same stable id.
    """

    conn.execute("DELETE FROM knowledge_relationships")
    conn.execute("DELETE FROM knowledge_evidence")
    conn.execute("DELETE FROM knowledge_mentions")
    conn.execute("DELETE FROM knowledge_objects")


def clear_all(conn: sqlite3.Connection) -> None:
    """Remove everything including the review history (``consolidate --force``)."""

    clear_consolidated(conn)
    conn.execute("DELETE FROM knowledge_reviews")


def snapshot_object_statuses(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["id"]: row["status"]
        for row in conn.execute("SELECT id, status FROM knowledge_objects")
    }


def snapshot_relationship_statuses(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str, str], str]:
    return {
        (row["source_object"], row["predicate"], row["target_object"]): row[
            "review_status"
        ]
        for row in conn.execute(
            "SELECT source_object, predicate, target_object, review_status "
            "FROM knowledge_relationships"
        )
    }


def insert_object(
    conn: sqlite3.Connection,
    *,
    id: str,
    name: str,
    object_type: str,
    description: str,
    canonical_name: str,
    confidence: float,
    status: str,
    merge_confidence: float,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_objects(
            id, name, object_type, description, canonical_name,
            confidence, status, merge_confidence, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            id,
            name,
            object_type,
            description,
            canonical_name,
            confidence,
            status,
            merge_confidence,
            created_at,
            created_at,
        ),
    )


def insert_mention(
    conn: sqlite3.Connection,
    *,
    knowledge_object_id: str,
    artifact_id: str,
    confidence: float,
    source_text: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_mentions(
            knowledge_object_id, artifact_id, confidence, source_text, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (knowledge_object_id, artifact_id, confidence, source_text, created_at),
    )


def insert_evidence(
    conn: sqlite3.Connection,
    *,
    knowledge_object_id: str,
    artifact_id: str,
    quote: str,
    page_number: int | None,
    slide_number: int | None,
    confidence: float,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_evidence(
            knowledge_object_id, artifact_id, quote,
            page_number, slide_number, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            knowledge_object_id,
            artifact_id,
            quote,
            page_number,
            slide_number,
            confidence,
            created_at,
        ),
    )


def upsert_relationship(
    conn: sqlite3.Connection,
    *,
    source_object: str,
    predicate: str,
    target_object: str,
    confidence: float,
    evidence: str,
    review_status: str,
    created_at: str,
) -> bool:
    """Insert or update one relationship. Returns True if newly created.

    Deduplicated on ``(source_object, predicate, target_object)``; a repeat
    bumps confidence to the max seen and refreshes evidence/timestamp without
    clobbering a human review_status that is already past PROPOSED.
    """

    row = conn.execute(
        """
        SELECT id, confidence, review_status FROM knowledge_relationships
        WHERE source_object = ? AND predicate = ? AND target_object = ?
        """,
        (source_object, predicate, target_object),
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO knowledge_relationships(
                source_object, predicate, target_object,
                confidence, evidence, review_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_object,
                predicate,
                target_object,
                confidence,
                evidence,
                review_status,
                created_at,
                created_at,
            ),
        )
        return False

    new_conf = max(confidence, row["confidence"] or 0.0)
    # Preserve a human decision; only a PROPOSED row adopts the incoming status.
    status = row["review_status"]
    if status == ReviewState.PROPOSED.value:
        status = review_status
    conn.execute(
        """
        UPDATE knowledge_relationships
        SET confidence = ?, evidence = ?, review_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_conf, evidence, status, created_at, row["id"]),
    )
    return False


def record_review(
    conn: sqlite3.Connection,
    *,
    target_kind: str,
    target_id: str,
    action: str,
    confidence: float | None,
    note: str,
    reviewer: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_reviews(
            target_kind, target_id, action, confidence, note, reviewer, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (target_kind, target_id, action, confidence, note, reviewer, created_at),
    )


def review_actions_for(conn: sqlite3.Connection, target_id: str) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT action FROM knowledge_reviews WHERE target_id = ? ORDER BY id",
        (target_id,),
    ).fetchall()
    return tuple(r["action"] for r in rows)


def reviews_for_target(
    conn: sqlite3.Connection, target_kind: str, target_id: str
) -> list[sqlite3.Row]:
    """Full ordered review history for one target (oldest first)."""

    return conn.execute(
        "SELECT * FROM knowledge_reviews WHERE target_kind = ? AND target_id = ? "
        "ORDER BY id",
        (target_kind, target_id),
    ).fetchall()


def agent_reviews(
    conn: sqlite3.Connection,
    *,
    reviewer_prefix: str,
    since: str | None = None,
) -> list[sqlite3.Row]:
    """Latest agent-attributed review per target, newest first.

    Used by the bulk agent-undo: it returns one row per target (the agent's most
    recent action on it) so a batch of agent decisions can be rolled back. Pass an
    exact ``agent:<name>`` to scope to one agent, or the bare ``agent:`` prefix
    for all agents.
    """

    like = f"{reviewer_prefix}%" if reviewer_prefix.endswith(":") else reviewer_prefix
    where = ["reviewer LIKE ?"]
    params: list[object] = [like]
    if since:
        where.append("created_at >= ?")
        params.append(since)
    return conn.execute(
        "SELECT target_kind, target_id, MAX(id) AS id, reviewer, created_at "
        "FROM knowledge_reviews WHERE " + " AND ".join(where) + " "
        "GROUP BY target_kind, target_id ORDER BY id DESC",
        params,
    ).fetchall()


def get_relationship(
    conn: sqlite3.Connection, relationship_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM knowledge_relationships WHERE id = ?", (relationship_id,)
    ).fetchone()


def set_object_status(
    conn: sqlite3.Connection, object_id: str, status: str, updated_at: str
) -> bool:
    cur = conn.execute(
        "UPDATE knowledge_objects SET status = ?, updated_at = ? WHERE id = ?",
        (status, updated_at, object_id),
    )
    return cur.rowcount > 0


def set_relationship_status(
    conn: sqlite3.Connection, relationship_id: int, status: str, updated_at: str
) -> bool:
    cur = conn.execute(
        "UPDATE knowledge_relationships SET review_status = ?, updated_at = ? WHERE id = ?",
        (status, updated_at, relationship_id),
    )
    return cur.rowcount > 0


# -- read side ----------------------------------------------------------------

def count_objects(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM knowledge_objects").fetchone()[0])


def count_table(conn: sqlite3.Connection, table: str) -> int:
    allowed = {
        "knowledge_objects",
        "knowledge_mentions",
        "knowledge_evidence",
        "knowledge_relationships",
        "knowledge_reviews",
    }
    if table not in allowed:
        raise ValueError(f"Unsupported table: {table}")
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def get_object(conn: sqlite3.Connection, object_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM knowledge_objects WHERE id = ?", (object_id,)
    ).fetchone()


def all_objects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_objects ORDER BY confidence DESC, canonical_name"
    ).fetchall()


def search_objects(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    like = f"%{query.strip()}%"
    return conn.execute(
        """
        SELECT * FROM knowledge_objects
        WHERE name LIKE ? COLLATE NOCASE
           OR canonical_name LIKE ? COLLATE NOCASE
           OR description LIKE ? COLLATE NOCASE
        ORDER BY confidence DESC, canonical_name
        """,
        (like, like, like),
    ).fetchall()


def objects_by_status(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_objects WHERE status = ? "
        "ORDER BY confidence DESC, canonical_name",
        (status,),
    ).fetchall()


def objects_in_confidence_interval(
    conn: sqlite3.Connection,
    min_confidence: float,
    max_confidence: float,
    *,
    status: str | None = None,
) -> list[sqlite3.Row]:
    """Return objects whose confidence falls in the inclusive interval."""

    where = ["confidence >= ?", "confidence <= ?"]
    params: list[object] = [min_confidence, max_confidence]
    if status:
        where.append("status = ?")
        params.append(status)
    return conn.execute(
        "SELECT * FROM knowledge_objects WHERE "
        + " AND ".join(where)
        + " ORDER BY confidence DESC, canonical_name",
        params,
    ).fetchall()


def object_type_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT object_type AS key, COUNT(*) AS count "
        "FROM knowledge_objects GROUP BY object_type ORDER BY count DESC, key"
    ).fetchall()


def mentions_for_object(conn: sqlite3.Connection, object_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_mentions WHERE knowledge_object_id = ? "
        "ORDER BY confidence DESC",
        (object_id,),
    ).fetchall()


def evidence_for_object(conn: sqlite3.Connection, object_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_evidence WHERE knowledge_object_id = ? "
        "ORDER BY confidence DESC",
        (object_id,),
    ).fetchall()


def relationships_for_object(
    conn: sqlite3.Connection, object_id: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_relationships "
        "WHERE source_object = ? OR target_object = ? "
        "ORDER BY confidence DESC",
        (object_id, object_id),
    ).fetchall()


def all_relationships(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_relationships ORDER BY confidence DESC"
    ).fetchall()


def relationships_by_status(
    conn: sqlite3.Connection, status: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM knowledge_relationships WHERE review_status = ? "
        "ORDER BY confidence DESC",
        (status,),
    ).fetchall()


def relationships_in_confidence_interval(
    conn: sqlite3.Connection,
    min_confidence: float,
    max_confidence: float,
    *,
    review_status: str | None = None,
) -> list[sqlite3.Row]:
    """Return relationships whose confidence falls in the inclusive interval."""

    where = ["confidence >= ?", "confidence <= ?"]
    params: list[object] = [min_confidence, max_confidence]
    if review_status:
        where.append("review_status = ?")
        params.append(review_status)
    return conn.execute(
        "SELECT * FROM knowledge_relationships WHERE "
        + " AND ".join(where)
        + " ORDER BY confidence DESC, id",
        params,
    ).fetchall()


# -- RDF projection (Prompt #7): only APPROVED data is exported ----------------

def approved_objects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return APPROVED knowledge objects in stable id order, for RDF export."""

    return conn.execute(
        "SELECT * FROM knowledge_objects WHERE status = ? ORDER BY id",
        (ReviewState.APPROVED.value,),
    ).fetchall()


def approved_relationships(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """APPROVED relationships whose source and target objects are both APPROVED.

    A relationship is only meaningful in the projection if both endpoints made it
    into the exported knowledge graph, so endpoints are constrained to APPROVED.
    """

    return conn.execute(
        """
        SELECT r.* FROM knowledge_relationships r
        JOIN knowledge_objects s ON s.id = r.source_object AND s.status = ?
        JOIN knowledge_objects t ON t.id = r.target_object AND t.status = ?
        WHERE r.review_status = ?
        ORDER BY r.id
        """,
        (ReviewState.APPROVED.value,) * 3,
    ).fetchall()


def evidence_for_approved_objects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Evidence rows for APPROVED objects, carrying the object's type and id.

    The extra ``object_type`` / ``object_id`` columns let the RDF layer mint the
    ``kg:supportedBy`` link without a second lookup.
    """

    return conn.execute(
        """
        SELECT e.*, o.object_type AS object_type, o.id AS object_id
        FROM knowledge_evidence e
        JOIN knowledge_objects o ON o.id = e.knowledge_object_id AND o.status = ?
        ORDER BY e.id
        """,
        (ReviewState.APPROVED.value,),
    ).fetchall()


__all__ = [
    "gather_mentions",
    "gather_candidate_equations",
    "gather_candidate_requirements",
    "gather_candidate_relationships",
    "clear_consolidated",
    "clear_all",
    "snapshot_object_statuses",
    "snapshot_relationship_statuses",
    "insert_object",
    "insert_mention",
    "insert_evidence",
    "upsert_relationship",
    "record_review",
    "review_actions_for",
    "set_object_status",
    "set_relationship_status",
    "count_objects",
    "count_table",
    "get_object",
    "all_objects",
    "search_objects",
    "objects_by_status",
    "objects_in_confidence_interval",
    "object_type_counts",
    "mentions_for_object",
    "evidence_for_object",
    "relationships_for_object",
    "all_relationships",
    "relationships_by_status",
    "relationships_in_confidence_interval",
    "approved_objects",
    "approved_relationships",
    "evidence_for_approved_objects",
]
