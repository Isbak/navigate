"""Persistence for the semantic classification layer.

This module owns every SQL statement that touches the semantic tables
(``document_classifications`` and the ``candidate_*`` tables) plus
``classification_runs``. It knows nothing about prompts, providers, or parsing;
the service layer hands it already-validated records.

Re-classifying a document is a clean replace: all of its prior semantic rows are
deleted and re-inserted inside one transaction, so the database always reflects
a single, coherent classification per artifact.
"""

from __future__ import annotations

import json
import sqlite3

from .models import ClassificationResult, KnowledgeType, ReviewStatus

# Candidate tables cleared when a document is (re)classified.
_CANDIDATE_TABLES = (
    "candidate_entities",
    "candidate_capabilities",
    "candidate_decisions",
    "candidate_risks",
    "candidate_relationships",
    "candidate_requirements",
    "candidate_equations",
)


def get_source_hash(conn: sqlite3.Connection, artifact_id: str) -> str | None:
    """Return the stored ``source_hash`` for an artifact, or None if unseen.

    Used for incremental processing: when the hash matches the current
    extraction, classification can be skipped.
    """

    row = conn.execute(
        "SELECT source_hash FROM document_classifications WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()
    return row["source_hash"] if row is not None else None


def has_classification(conn: sqlite3.Connection, artifact_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM document_classifications WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()
    return row is not None


def delete_for_artifact(conn: sqlite3.Connection, artifact_id: str) -> None:
    """Remove all semantic rows for an artifact ahead of a fresh classification."""

    conn.execute(
        "DELETE FROM document_classifications WHERE artifact_id = ?", (artifact_id,)
    )
    for table in _CANDIDATE_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE artifact_id = ?", (artifact_id,))


def persist_classification(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    result: ClassificationResult,
    model: str,
    source_hash: str,
    created_at: str,
) -> None:
    """Insert one document's full classification and candidate rows.

    The caller is expected to have called :func:`delete_for_artifact` first (the
    service does this within the same transaction) so this is a clean insert.
    Every row is stamped with provenance, a knowledge_type, and review_status
    NEW; nothing is ever written as a FACT.
    """

    new = ReviewStatus.NEW.value
    obs = KnowledgeType.OBSERVATION.value
    hyp = KnowledgeType.HYPOTHESIS.value

    conn.execute(
        """
        INSERT INTO document_classifications(
            artifact_id, document_type, type_confidence, domains,
            short_summary, long_summary, knowledge_type, review_status,
            model, source_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            result.document_type,
            result.type_confidence,
            json.dumps(
                [{"domain": d.domain, "confidence": d.confidence} for d in result.domains]
            ),
            result.short_summary,
            result.long_summary,
            obs,
            new,
            model,
            source_hash,
            created_at,
        ),
    )

    conn.executemany(
        """
        INSERT INTO candidate_entities(
            artifact_id, entity_type, name, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (artifact_id, e.entity_type, e.name, e.confidence, e.supporting_text,
             obs, new, model, created_at)
            for e in result.entities
        ],
    )

    conn.executemany(
        """
        INSERT INTO candidate_capabilities(
            artifact_id, name, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (artifact_id, c.name, c.confidence, c.supporting_text,
             obs, new, model, created_at)
            for c in result.capabilities
        ],
    )

    conn.executemany(
        """
        INSERT INTO candidate_decisions(
            artifact_id, decision_text, title, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (artifact_id, d.decision_text, d.title, d.confidence, d.supporting_text,
             hyp, new, model, created_at)
            for d in result.decisions
        ],
    )

    conn.executemany(
        """
        INSERT INTO candidate_risks(
            artifact_id, risk_description, title, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (artifact_id, r.risk_description, r.title, r.confidence, r.supporting_text,
             hyp, new, model, created_at)
            for r in result.risks
        ],
    )

    conn.executemany(
        """
        INSERT INTO candidate_relationships(
            artifact_id, subject, predicate, object, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (artifact_id, rel.subject, rel.predicate, rel.object, rel.confidence,
             rel.supporting_text, hyp, new, model, created_at)
            for rel in result.relationships
        ],
    )

    conn.executemany(
        """
        INSERT INTO candidate_requirements(
            artifact_id, standard_name, standard_version, clause_ref, title,
            requirement_text, obligation_level, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (artifact_id, rq.standard_name, rq.standard_version, rq.clause_ref,
             rq.title, rq.text, rq.obligation_level, rq.confidence,
             rq.supporting_text, obs, new, model, created_at)
            for rq in result.requirements
        ],
    )

    conn.executemany(
        """
        INSERT INTO candidate_equations(
            artifact_id, standard_name, standard_version, clause_ref, symbol,
            title, expression, python_code, ast_json, variables, latex, valid,
            validation_note, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (artifact_id, eq.standard_name, eq.standard_version, eq.clause_ref,
             eq.symbol, eq.title, eq.expression, eq.python_code, eq.ast_json,
             json.dumps(eq.variables), eq.latex, 1 if eq.valid else 0,
             eq.validation_note, eq.confidence, eq.supporting_text,
             obs, new, model, created_at)
            for eq in result.equations
        ],
    )


def record_classification_run(
    conn: sqlite3.Connection,
    *,
    started_at: str,
    completed_at: str,
    model: str,
    documents_processed: int,
    documents_skipped: int,
    errors: int,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO classification_runs(
            started_at, completed_at, model,
            documents_processed, documents_skipped, errors
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (started_at, completed_at, model, documents_processed, documents_skipped, errors),
    )
    return int(cur.lastrowid)


def latest_classification_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM classification_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()


# -- read helpers used by reporting / CLI / analytics --------------------------

def count_classifications(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM document_classifications").fetchone()[0]
    )


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    if table not in _CANDIDATE_TABLES and table != "document_classifications":
        raise ValueError(f"Unsupported table: {table}")
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def document_type_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT document_type AS key, COUNT(*) AS count "
        "FROM document_classifications GROUP BY document_type "
        "ORDER BY count DESC, key"
    ).fetchall()


def review_status_counts(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if table not in _CANDIDATE_TABLES and table != "document_classifications":
        raise ValueError(f"Unsupported table: {table}")
    return conn.execute(
        f"SELECT review_status AS key, COUNT(*) AS count "
        f"FROM {table} GROUP BY review_status ORDER BY count DESC, key"
    ).fetchall()


def get_classification(conn: sqlite3.Connection, artifact_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM document_classifications WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()


def decisions(conn: sqlite3.Connection, min_confidence: float = 0.0) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM candidate_decisions WHERE confidence >= ? "
        "ORDER BY confidence DESC, artifact_id",
        (min_confidence,),
    ).fetchall()


def risks(conn: sqlite3.Connection, min_confidence: float = 0.0) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM candidate_risks WHERE confidence >= ? "
        "ORDER BY confidence DESC, artifact_id",
        (min_confidence,),
    ).fetchall()


def capabilities(conn: sqlite3.Connection, min_confidence: float = 0.0) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM candidate_capabilities WHERE confidence >= ? "
        "ORDER BY confidence DESC, name",
        (min_confidence,),
    ).fetchall()


def relationships(conn: sqlite3.Connection, min_confidence: float = 0.0) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM candidate_relationships WHERE confidence >= ? "
        "ORDER BY confidence DESC, subject",
        (min_confidence,),
    ).fetchall()


def entities(conn: sqlite3.Connection, min_confidence: float = 0.0) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM candidate_entities WHERE confidence >= ? "
        "ORDER BY confidence DESC, name",
        (min_confidence,),
    ).fetchall()


def all_classifications(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM document_classifications ORDER BY artifact_id"
    ).fetchall()


__all__ = [
    "get_source_hash",
    "has_classification",
    "delete_for_artifact",
    "persist_classification",
    "record_classification_run",
    "latest_classification_run",
    "count_classifications",
    "count_rows",
    "document_type_counts",
    "review_status_counts",
    "get_classification",
    "decisions",
    "risks",
    "capabilities",
    "relationships",
    "entities",
    "all_classifications",
]
