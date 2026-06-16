"""Shared fixtures for the knowledge-graph explorer tests (Prompt #8).

``approved_graph`` seeds a small but realistically connected approved knowledge
base - the documented Release Governance example - so the SPARQL, path, impact,
neighbour, metrics, health, and export tests all exercise the same graph:

    Release Governance --supports--> Launchpad Model
    Release Governance --related_to--> Release Management
    Release Governance --owned_by--> Test & Release Team
    Release Management --supports--> Launchpad Model
    Salesforce --affects--> Release Management
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from catalog.db import connect, init_db
from catalog.knowledge.models import ReviewState
from catalog.knowledge.service import consolidate, review_object, review_relationship


def _seed_candidates(conn) -> None:
    def capability(artifact, name, confidence, text):
        conn.execute(
            "INSERT INTO candidate_capabilities(artifact_id, name, confidence, "
            "supporting_text, knowledge_type, review_status, model, created_at) "
            "VALUES (?, ?, ?, ?, 'OBSERVATION', 'NEW', 'stub', 't')",
            (artifact, name, confidence, text),
        )

    def decision(artifact, text, confidence, support):
        conn.execute(
            "INSERT INTO candidate_decisions(artifact_id, decision_text, confidence, "
            "supporting_text, knowledge_type, review_status, model, created_at) "
            "VALUES (?, ?, ?, ?, 'OBSERVATION', 'NEW', 'stub', 't')",
            (artifact, text, confidence, support),
        )

    def entity(artifact, etype, name, confidence, text):
        conn.execute(
            "INSERT INTO candidate_entities(artifact_id, entity_type, name, confidence, "
            "supporting_text, review_status, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'NEW', 'stub', 't')",
            (artifact, etype, name, confidence, text),
        )

    def relationship(artifact, subject, predicate, obj, confidence=0.9):
        conn.execute(
            "INSERT INTO candidate_relationships(artifact_id, subject, predicate, object, "
            "confidence, supporting_text, review_status, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'rel evidence', 'NEW', 'stub', 't')",
            (artifact, subject, predicate, obj, confidence),
        )

    capability("doc_a", "Release Governance", 0.94, "we run release governance")
    capability("doc_b", "Release Management", 0.88, "release management practice")
    decision("doc_a", "Launchpad Model", 0.90, "adopt the launchpad model")
    entity("doc_a", "Team", "Test & Release Team", 0.85, "owned by the test and release team")
    entity("doc_b", "Platform", "Salesforce", 0.80, "salesforce platform")

    relationship("doc_a", "Release Governance", "supports", "Launchpad Model")
    relationship("doc_b", "Release Governance", "related_to", "Release Management")
    relationship("doc_a", "Release Governance", "owned_by", "Test & Release Team")
    relationship("doc_b", "Release Management", "supports", "Launchpad Model")
    relationship("doc_b", "Salesforce", "affects", "Release Management")


def _approve_everything(db) -> None:
    with connect(db) as conn:
        object_ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_objects")]
    for object_id in object_ids:
        review_object(db, object_id, ReviewState.APPROVED.value)
    with connect(db) as conn:
        rel_ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_relationships")]
    for rel_id in rel_ids:
        review_relationship(db, rel_id, ReviewState.APPROVED.value)


@dataclass
class SeededGraph:
    db: str
    object_ids: list[str]


@pytest.fixture
def approved_graph(tmp_path) -> SeededGraph:
    db = str(tmp_path / "catalog.sqlite")
    init_db(db)
    with connect(db) as conn:
        _seed_candidates(conn)
        conn.commit()
    consolidate(db)
    _approve_everything(db)
    with connect(db) as conn:
        object_ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_objects")]
    return SeededGraph(db=db, object_ids=object_ids)


# -- governance fixtures (Prompt #10) -----------------------------------------

def _seed_governance_candidates(conn) -> None:
    """Seed the same graph as ``approved_graph`` plus document classifications.

    Classifications carry the domains that domain governance maps objects onto,
    so the governed fixture exercises domain health too.
    """

    _seed_candidates(conn)
    # Document classifications give objects a domain via their mentions.
    conn.execute(
        "INSERT INTO document_classifications(artifact_id, document_type, "
        "type_confidence, domains, short_summary, long_summary, model, created_at) "
        "VALUES('doc_a','strategy',0.9,?,'s','l','stub','t')",
        ('[{"domain": "Test & Release", "confidence": 0.9}]',),
    )
    conn.execute(
        "INSERT INTO document_classifications(artifact_id, document_type, "
        "type_confidence, domains, short_summary, long_summary, model, created_at) "
        "VALUES('doc_b','strategy',0.9,?,'s','l','stub','t')",
        ('[{"domain": "Operations", "confidence": 0.8}]',),
    )


@pytest.fixture
def governed_db(tmp_path) -> str:
    """A consolidated graph with one governance scan already run."""

    from catalog.governance.config import load_governance_config
    from catalog.governance.service import run_scan

    db = str(tmp_path / "catalog.sqlite")
    init_db(db)
    with connect(db) as conn:
        _seed_governance_candidates(conn)
        conn.commit()
    consolidate(db)
    run_scan(db, load_governance_config("config/governance.yml"))
    return db


# -- compliance fixtures ------------------------------------------------------

def _seed_compliance_candidates(conn) -> None:
    """Seed two GDPR requirements and one control that satisfies one of them.

    GDPR Art. 32 is satisfied by the "Encryption at rest" capability (with
    evidence); GDPR Art. 30 has no satisfying control, so it is an open gap.
    """

    def requirement(clause, title, text, standard="GDPR", version="2016"):
        conn.execute(
            "INSERT INTO candidate_requirements(artifact_id, standard_name, "
            "standard_version, clause_ref, title, requirement_text, obligation_level, "
            "confidence, supporting_text, knowledge_type, review_status, model, created_at) "
            "VALUES('imp', ?, ?, ?, ?, ?, 'MANDATORY', 0.95, ?, 'OBSERVATION', 'NEW', "
            "'curated_import', 't')",
            (standard, version, clause, title, text, text),
        )

    requirement("Art. 32", "Security of processing",
                "The controller shall implement appropriate technical measures.")
    requirement("Art. 30", "Records of processing activities",
                "Each controller shall maintain a record of processing activities.")
    conn.execute(
        "INSERT INTO candidate_capabilities(artifact_id, name, confidence, "
        "supporting_text, knowledge_type, review_status, model, created_at) "
        "VALUES('doc_c', 'Encryption at rest', 0.92, "
        "'all data is encrypted at rest with AES-256', 'OBSERVATION', 'NEW', 'stub', 't')",
    )
    conn.execute(
        "INSERT INTO candidate_relationships(artifact_id, subject, predicate, object, "
        "confidence, supporting_text, review_status, model, created_at) "
        "VALUES('doc_c', 'Encryption at rest', 'satisfies', 'GDPR Art. 32', 0.9, "
        "'encryption satisfies the security requirement', 'NEW', 'stub', 't')",
    )


@pytest.fixture
def compliance_db(tmp_path) -> str:
    """A consolidated graph with a standard, requirements, and an approved control.

    The "Encryption at rest" control object and its ``satisfies`` edge are
    APPROVED so the engine can reach a SATISFIED status for GDPR Art. 32; the
    assessment itself is left PROPOSED for the test to approve.
    """

    db = str(tmp_path / "catalog.sqlite")
    init_db(db)
    with connect(db) as conn:
        _seed_compliance_candidates(conn)
        conn.commit()
    consolidate(db)
    with connect(db) as conn:
        conn.execute(
            "UPDATE knowledge_objects SET status='APPROVED' "
            "WHERE id='capability_encryption_at_rest'"
        )
        conn.execute(
            "UPDATE knowledge_relationships SET review_status='APPROVED' "
            "WHERE predicate='satisfies'"
        )
        conn.commit()
    return db
