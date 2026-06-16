"""Tests for the compliance assessment engine."""

from __future__ import annotations

from catalog.compliance.config import ComplianceConfig
from catalog.compliance.service import assess, review_assessment
from catalog.compliance.sync import sync_requirements
from catalog.db import connect, init_db
from catalog.knowledge.service import consolidate


def _seed_one(conn, *, approve_control: bool) -> None:
    conn.execute(
        "INSERT INTO candidate_requirements(artifact_id, standard_name, "
        "standard_version, clause_ref, title, requirement_text, obligation_level, "
        "confidence, supporting_text, review_status, model, created_at) "
        "VALUES('imp','GDPR','2016','Art. 32','Security','shall secure','MANDATORY',"
        "0.95,'shall secure','NEW','curated_import','t')",
    )
    conn.execute(
        "INSERT INTO candidate_capabilities(artifact_id, name, confidence, "
        "supporting_text, review_status, model, created_at) "
        "VALUES('doc','Encryption at rest',0.9,'data encrypted at rest','NEW','stub','t')",
    )
    conn.execute(
        "INSERT INTO candidate_relationships(artifact_id, subject, predicate, object, "
        "confidence, supporting_text, review_status, model, created_at) "
        "VALUES('doc','Encryption at rest','satisfies','GDPR Art. 32',0.9,'proof','NEW','stub','t')",
    )


def _build(tmp_path, *, approve_control: bool):
    db = str(tmp_path / "c.sqlite")
    init_db(db)
    with connect(db) as conn:
        _seed_one(conn, approve_control=approve_control)
        conn.commit()
    consolidate(db)
    with connect(db) as conn:
        conn.execute("UPDATE knowledge_relationships SET review_status='APPROVED' "
                     "WHERE predicate='satisfies'")
        if approve_control:
            conn.execute("UPDATE knowledge_objects SET status='APPROVED' "
                         "WHERE id='capability_encryption_at_rest'")
        conn.commit()
    return db


def test_assess_satisfied_for_approved_control(compliance_db):
    stats = assess(compliance_db)
    assert stats.requirements_assessed == 2  # Art. 32 + Art. 30
    assert stats.satisfied == 1
    assert stats.gaps == 1
    with connect(compliance_db) as conn:
        row = conn.execute(
            "SELECT status FROM compliance_assessments "
            "WHERE requirement_object_id='requirement_gdpr_art_32'"
        ).fetchone()
    assert row["status"] == "SATISFIED"


def test_satisfied_assessment_has_evidence(compliance_db):
    assess(compliance_db)
    with connect(compliance_db) as conn:
        a = conn.execute(
            "SELECT id FROM compliance_assessments WHERE status='SATISFIED'"
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) FROM compliance_assessment_evidence WHERE assessment_id=?",
            (a["id"],),
        ).fetchone()[0]
    assert count >= 1


def test_unapproved_control_is_partial(tmp_path):
    db = _build(tmp_path, approve_control=False)
    assess(db)
    with connect(db) as conn:
        row = conn.execute(
            "SELECT status FROM compliance_assessments "
            "WHERE requirement_object_id='requirement_gdpr_art_32'"
        ).fetchone()
    assert row["status"] == "PARTIAL"


def test_stale_evidence_downgrades_to_partial(compliance_db):
    # Age the control's evidence well past the staleness horizon.
    with connect(compliance_db) as conn:
        conn.execute(
            "UPDATE knowledge_evidence SET created_at='2000-01-01T00:00:00+00:00' "
            "WHERE knowledge_object_id='capability_encryption_at_rest'"
        )
        conn.commit()
    assess(compliance_db, ComplianceConfig(stale_evidence_days=30))
    with connect(compliance_db) as conn:
        row = conn.execute(
            "SELECT status FROM compliance_assessments "
            "WHERE requirement_object_id='requirement_gdpr_art_32'"
        ).fetchone()
    assert row["status"] == "PARTIAL"


def test_rerun_preserves_human_review(compliance_db):
    assess(compliance_db)
    with connect(compliance_db) as conn:
        a = conn.execute(
            "SELECT id FROM compliance_assessments WHERE status='SATISFIED'"
        ).fetchone()
    review_assessment(compliance_db, a["id"], "APPROVED")

    # Re-run: the (requirement, control) pair keeps its APPROVED review status.
    assess(compliance_db)
    with connect(compliance_db) as conn:
        row = conn.execute(
            "SELECT review_status FROM compliance_assessments "
            "WHERE requirement_object_id='requirement_gdpr_art_32' "
            "AND status='SATISFIED'"
        ).fetchone()
    assert row["review_status"] == "APPROVED"


def test_sync_creates_requirement_metadata(compliance_db):
    with connect(compliance_db) as conn:
        synced = sync_requirements(conn, "t")
        conn.commit()
        meta = conn.execute(
            "SELECT clause_ref, obligation_level FROM compliance_requirements "
            "WHERE object_id='requirement_gdpr_art_32'"
        ).fetchone()
    assert synced >= 2
    assert meta["clause_ref"] == "Art. 32"
    assert meta["obligation_level"] == "MANDATORY"
