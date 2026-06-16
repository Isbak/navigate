"""Persistence for the compliance layer.

Owns every SQL statement touching the compliance tables
(``compliance_standards``, ``compliance_requirements``,
``compliance_assessments``, ``compliance_assessment_evidence``,
``compliance_runs``) and the read-only joins against the knowledge graph it needs
(the ``satisfies`` relationships and the controls' evidence).

Functions are connection-first and never manage transactions; the service layer
opens the connection and commits. Nothing here derives a status or enforces an
invariant - that is the service's job.
"""

from __future__ import annotations

import sqlite3

from .models import ComplianceReviewState

# Predicate that links a control to the requirement it claims to meet.
SATISFIES = "satisfies"
MANDATED_BY = "mandated_by"


# -- standards & requirements (enrichment) ------------------------------------

def upsert_standard(
    conn: sqlite3.Connection,
    *,
    object_id: str,
    name: str,
    authority: str,
    version: str,
    jurisdiction: str,
    effective_from: str,
    source_url: str,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO compliance_standards(
            object_id, name, authority, version, jurisdiction,
            effective_from, source_url, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(object_id) DO UPDATE SET
            name=excluded.name, authority=excluded.authority,
            version=excluded.version, jurisdiction=excluded.jurisdiction,
            effective_from=excluded.effective_from, source_url=excluded.source_url,
            updated_at=excluded.updated_at
        """,
        (object_id, name, authority, version, jurisdiction,
         effective_from, source_url, now, now),
    )


def upsert_requirement(
    conn: sqlite3.Connection,
    *,
    object_id: str,
    standard_object_id: str,
    clause_ref: str,
    title: str,
    requirement_text: str,
    obligation_level: str,
    assessed_against_version: str,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO compliance_requirements(
            object_id, standard_object_id, clause_ref, title, requirement_text,
            obligation_level, assessed_against_version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(object_id) DO UPDATE SET
            standard_object_id=excluded.standard_object_id,
            clause_ref=excluded.clause_ref, title=excluded.title,
            requirement_text=excluded.requirement_text,
            obligation_level=excluded.obligation_level,
            assessed_against_version=excluded.assessed_against_version,
            updated_at=excluded.updated_at
        """,
        (object_id, standard_object_id, clause_ref, title, requirement_text,
         obligation_level, assessed_against_version, now, now),
    )


def upsert_equation(
    conn: sqlite3.Connection,
    *,
    object_id: str,
    standard_object_id: str,
    requirement_object_id: str,
    clause_ref: str,
    symbol: str,
    title: str,
    expression: str,
    python_code: str,
    ast_json: str,
    variables: str,
    latex: str,
    valid: bool,
    validation_note: str,
    assessed_against_version: str,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO compliance_equations(
            object_id, standard_object_id, requirement_object_id, clause_ref,
            symbol, title, expression, python_code, ast_json, variables, latex,
            valid, validation_note, assessed_against_version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(object_id) DO UPDATE SET
            standard_object_id=excluded.standard_object_id,
            requirement_object_id=excluded.requirement_object_id,
            clause_ref=excluded.clause_ref, symbol=excluded.symbol,
            title=excluded.title, expression=excluded.expression,
            python_code=excluded.python_code, ast_json=excluded.ast_json,
            variables=excluded.variables, latex=excluded.latex,
            valid=excluded.valid, validation_note=excluded.validation_note,
            assessed_against_version=excluded.assessed_against_version,
            updated_at=excluded.updated_at
        """,
        (object_id, standard_object_id, requirement_object_id, clause_ref, symbol,
         title, expression, python_code, ast_json, variables, latex,
         1 if valid else 0, validation_note, assessed_against_version, now, now),
    )


def equations(
    conn: sqlite3.Connection, standard_object_id: str | None = None
) -> list[sqlite3.Row]:
    sql = """
        SELECT e.*, o.name AS object_name, o.status AS object_status
        FROM compliance_equations e
        LEFT JOIN knowledge_objects o ON o.id = e.object_id
    """
    params: tuple = ()
    if standard_object_id:
        sql += " WHERE e.standard_object_id = ?"
        params = (standard_object_id,)
    sql += " ORDER BY e.standard_object_id, e.clause_ref, e.symbol, e.object_id"
    return conn.execute(sql, params).fetchall()


def get_equation(conn: sqlite3.Connection, object_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT e.*, o.name AS object_name, o.status AS object_status
        FROM compliance_equations e
        LEFT JOIN knowledge_objects o ON o.id = e.object_id
        WHERE e.object_id = ?
        """,
        (object_id,),
    ).fetchone()


def find_equation_id(conn: sqlite3.Connection, term: str) -> str | None:
    """Resolve a free-text term to an equation object id.

    Matches the object id exactly, then the symbol, then the clause ref, then a
    case-insensitive fragment of the object name.
    """

    row = conn.execute(
        "SELECT object_id FROM compliance_equations WHERE object_id = ?", (term,)
    ).fetchone()
    if row:
        return row["object_id"]
    row = conn.execute(
        "SELECT object_id FROM compliance_equations WHERE symbol = ? LIMIT 1", (term,)
    ).fetchone()
    if row:
        return row["object_id"]
    row = conn.execute(
        "SELECT object_id FROM compliance_equations WHERE clause_ref = ? LIMIT 1",
        (term,),
    ).fetchone()
    if row:
        return row["object_id"]
    row = conn.execute(
        """
        SELECT e.object_id
        FROM compliance_equations e
        LEFT JOIN knowledge_objects o ON o.id = e.object_id
        WHERE LOWER(o.name) LIKE '%' || LOWER(?) || '%'
           OR LOWER(e.title) LIKE '%' || LOWER(?) || '%'
        ORDER BY LENGTH(o.name)
        LIMIT 1
        """,
        (term, term),
    ).fetchone()
    return row["object_id"] if row else None


def existing_object_ids(conn: sqlite3.Connection) -> set[str]:
    """Ids of knowledge objects that currently exist (for soft-ref validation)."""

    return {r["id"] for r in conn.execute("SELECT id FROM knowledge_objects")}


def standards(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.*, o.name AS object_name, o.status AS object_status
        FROM compliance_standards s
        LEFT JOIN knowledge_objects o ON o.id = s.object_id
        ORDER BY s.name, s.object_id
        """
    ).fetchall()


def get_standard(conn: sqlite3.Connection, object_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM compliance_standards WHERE object_id = ?", (object_id,)
    ).fetchone()


def requirements(
    conn: sqlite3.Connection, standard_object_id: str | None = None
) -> list[sqlite3.Row]:
    sql = """
        SELECT r.*, o.name AS object_name, o.status AS object_status
        FROM compliance_requirements r
        LEFT JOIN knowledge_objects o ON o.id = r.object_id
    """
    params: tuple = ()
    if standard_object_id:
        sql += " WHERE r.standard_object_id = ?"
        params = (standard_object_id,)
    sql += " ORDER BY r.standard_object_id, r.clause_ref, r.object_id"
    return conn.execute(sql, params).fetchall()


def get_requirement(conn: sqlite3.Connection, object_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT r.*, o.name AS object_name, o.status AS object_status
        FROM compliance_requirements r
        LEFT JOIN knowledge_objects o ON o.id = r.object_id
        WHERE r.object_id = ?
        """,
        (object_id,),
    ).fetchone()


def find_requirement_id(conn: sqlite3.Connection, term: str) -> str | None:
    """Resolve a free-text term to a requirement object id.

    Matches the object id exactly, then the clause ref, then a case-insensitive
    fragment of the object name (e.g. "Art. 32" or "gdpr art 32").
    """

    row = conn.execute(
        "SELECT object_id FROM compliance_requirements WHERE object_id = ?", (term,)
    ).fetchone()
    if row:
        return row["object_id"]
    row = conn.execute(
        "SELECT object_id FROM compliance_requirements WHERE clause_ref = ? LIMIT 1",
        (term,),
    ).fetchone()
    if row:
        return row["object_id"]
    row = conn.execute(
        """
        SELECT r.object_id
        FROM compliance_requirements r
        LEFT JOIN knowledge_objects o ON o.id = r.object_id
        WHERE LOWER(o.name) LIKE '%' || LOWER(?) || '%'
           OR LOWER(r.title) LIKE '%' || LOWER(?) || '%'
        ORDER BY LENGTH(o.name)
        LIMIT 1
        """,
        (term, term),
    ).fetchone()
    return row["object_id"] if row else None


# -- controls (read the knowledge graph) --------------------------------------

def satisfying_controls(
    conn: sqlite3.Connection,
    requirement_object_id: str,
    *,
    control_types: tuple[str, ...],
    only_approved: bool,
) -> list[sqlite3.Row]:
    """Return control objects whose ``satisfies`` edge targets the requirement.

    A control is any knowledge object of an allowed type that is the *source* of
    a ``satisfies`` relationship to the requirement. When ``only_approved`` is
    set, the edge must be APPROVED - the platform's trust rule.
    """

    if not control_types:
        return []
    placeholders = ",".join("?" for _ in control_types)
    rel_clause = ""
    if only_approved:
        rel_clause = "AND rel.review_status = 'APPROVED'"
    return conn.execute(
        f"""
        SELECT o.id, o.name, o.object_type, o.status,
               rel.confidence AS rel_confidence, rel.review_status AS rel_review_status
        FROM knowledge_relationships rel
        JOIN knowledge_objects o ON o.id = rel.source_object
        WHERE rel.predicate = ?
          AND rel.target_object = ?
          AND rel.review_status != 'REJECTED'
          {rel_clause}
          AND o.object_type IN ({placeholders})
        ORDER BY rel.confidence DESC, o.id
        """,
        (SATISFIES, requirement_object_id, *control_types),
    ).fetchall()


def control_freshness(
    conn: sqlite3.Connection, control_object_id: str
) -> str | None:
    """Return the governance freshness state of a control, if scanned."""

    row = conn.execute(
        "SELECT freshness_state FROM knowledge_lifecycle WHERE object_id = ?",
        (control_object_id,),
    ).fetchone()
    return row["freshness_state"] if row else None


def control_evidence(
    conn: sqlite3.Connection, control_object_id: str
) -> list[sqlite3.Row]:
    """Evidence quotes attached to a control object (its proof of existence)."""

    return conn.execute(
        """
        SELECT artifact_id, quote, clause_ref, page_number, confidence, created_at
        FROM knowledge_evidence
        WHERE knowledge_object_id = ?
        ORDER BY confidence DESC, id
        """,
        (control_object_id,),
    ).fetchall()


# -- assessments --------------------------------------------------------------

def snapshot_assessment_reviews(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str], str]:
    """Map (requirement_id, control_id or '') -> review_status before a re-run."""

    return {
        (r["requirement_object_id"], r["control_object_id"] or ""): r["review_status"]
        for r in conn.execute(
            "SELECT requirement_object_id, control_object_id, review_status "
            "FROM compliance_assessments"
        )
    }


def clear_assessments(conn: sqlite3.Connection) -> None:
    """Remove all assessments and their evidence ahead of a fresh ``assess``."""

    conn.execute("DELETE FROM compliance_assessment_evidence")
    conn.execute("DELETE FROM compliance_assessments")


def insert_assessment(
    conn: sqlite3.Connection,
    *,
    requirement_object_id: str,
    control_object_id: str | None,
    status: str,
    assessed_against_version: str,
    rationale: str,
    assessor: str,
    review_status: str,
    now: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO compliance_assessments(
            requirement_object_id, control_object_id, status,
            assessed_against_version, rationale, assessor, assessed_at,
            review_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (requirement_object_id, control_object_id, status, assessed_against_version,
         rationale, assessor, now, review_status, now, now),
    )
    return int(cur.lastrowid)


def add_assessment_evidence(
    conn: sqlite3.Connection,
    *,
    assessment_id: int,
    artifact_id: str,
    quote: str,
    clause_ref: str,
    page_number: int | None,
    confidence: float,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO compliance_assessment_evidence(
            assessment_id, artifact_id, quote, clause_ref,
            page_number, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (assessment_id, artifact_id, quote, clause_ref, page_number, confidence, now),
    )


def get_assessment(conn: sqlite3.Connection, assessment_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM compliance_assessments WHERE id = ?", (assessment_id,)
    ).fetchone()


def assessment_evidence(
    conn: sqlite3.Connection, assessment_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM compliance_assessment_evidence WHERE assessment_id = ? "
        "ORDER BY confidence DESC, id",
        (assessment_id,),
    ).fetchall()


def assessments(
    conn: sqlite3.Connection, status: str | None = None
) -> list[sqlite3.Row]:
    sql = """
        SELECT a.*, ro.name AS requirement_name, co.name AS control_name
        FROM compliance_assessments a
        LEFT JOIN knowledge_objects ro ON ro.id = a.requirement_object_id
        LEFT JOIN knowledge_objects co ON co.id = a.control_object_id
    """
    params: tuple = ()
    if status:
        sql += " WHERE a.status = ?"
        params = (status,)
    sql += " ORDER BY a.requirement_object_id, a.id"
    return conn.execute(sql, params).fetchall()


def set_assessment_review(
    conn: sqlite3.Connection, assessment_id: int, review_status: str, now: str
) -> bool:
    cur = conn.execute(
        "UPDATE compliance_assessments SET review_status = ?, updated_at = ? WHERE id = ?",
        (review_status, now, assessment_id),
    )
    return cur.rowcount > 0


# -- analytics: coverage & gaps -----------------------------------------------

def coverage_by_standard(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per-standard requirement counts and how many are satisfied.

    A requirement counts as satisfied when it has at least one APPROVED
    assessment with status SATISFIED; partial/gap/unassessed do not.
    """

    return conn.execute(
        """
        SELECT
            r.standard_object_id AS standard_object_id,
            s.name AS standard_name,
            COUNT(DISTINCT r.object_id) AS total,
            COUNT(DISTINCT CASE WHEN a.status = 'SATISFIED' AND a.review_status = 'APPROVED'
                                THEN r.object_id END) AS satisfied,
            COUNT(DISTINCT CASE WHEN a.status = 'PARTIAL' AND a.review_status = 'APPROVED'
                                THEN r.object_id END) AS partial
        FROM compliance_requirements r
        LEFT JOIN compliance_standards s ON s.object_id = r.standard_object_id
        LEFT JOIN compliance_assessments a ON a.requirement_object_id = r.object_id
        GROUP BY r.standard_object_id, s.name
        ORDER BY s.name, r.standard_object_id
        """
    ).fetchall()


def open_gaps(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Requirements with no APPROVED satisfying control (a SATISFIED assessment).

    A requirement is a gap unless it has at least one APPROVED assessment whose
    status is SATISFIED. Requirements that are PARTIAL, GAP, UNASSESSED, or only
    proposed (not yet approved) are all open gaps.
    """

    return conn.execute(
        """
        SELECT r.object_id, r.clause_ref, r.title, r.obligation_level,
               r.standard_object_id, o.name AS requirement_name,
               s.name AS standard_name
        FROM compliance_requirements r
        LEFT JOIN knowledge_objects o ON o.id = r.object_id
        LEFT JOIN compliance_standards s ON s.object_id = r.standard_object_id
        WHERE NOT EXISTS (
            SELECT 1 FROM compliance_assessments a
            WHERE a.requirement_object_id = r.object_id
              AND a.status = 'SATISFIED'
              AND a.review_status = 'APPROVED'
        )
        ORDER BY r.standard_object_id, r.clause_ref, r.object_id
        """
    ).fetchall()


def record_run(
    conn: sqlite3.Connection,
    *,
    started_at: str,
    finished_at: str,
    requirements_assessed: int,
    satisfied: int,
    partial: int,
    gaps: int,
    not_applicable: int,
    coverage: float,
    errors: int,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO compliance_runs(
            started_at, finished_at, requirements_assessed, satisfied, partial,
            gaps, not_applicable, coverage, errors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (started_at, finished_at, requirements_assessed, satisfied, partial,
         gaps, not_applicable, coverage, errors),
    )
    return int(cur.lastrowid)


def latest_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM compliance_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()


__all__ = [
    "SATISFIES",
    "MANDATED_BY",
    "upsert_standard",
    "upsert_requirement",
    "upsert_equation",
    "equations",
    "get_equation",
    "find_equation_id",
    "existing_object_ids",
    "standards",
    "get_standard",
    "requirements",
    "get_requirement",
    "find_requirement_id",
    "satisfying_controls",
    "control_freshness",
    "control_evidence",
    "snapshot_assessment_reviews",
    "clear_assessments",
    "insert_assessment",
    "add_assessment_evidence",
    "get_assessment",
    "assessment_evidence",
    "assessments",
    "set_assessment_review",
    "coverage_by_standard",
    "open_gaps",
    "record_run",
    "latest_run",
]
