"""Tests for compliance coverage, gaps, and the prove-compliance proof."""

from __future__ import annotations

from catalog.compliance.service import assess, coverage, gaps, prove, review_assessment
from catalog.db import connect


def _approve_satisfied(db) -> int:
    with connect(db) as conn:
        a = conn.execute(
            "SELECT id FROM compliance_assessments WHERE status='SATISFIED'"
        ).fetchone()
    review_assessment(db, a["id"], "APPROVED")
    return a["id"]


def test_gaps_lists_unsatisfied_requirements(compliance_db):
    assess(compliance_db)
    # Before any approval, every requirement is an open gap.
    ids = {g["object_id"] for g in gaps(compliance_db)}
    assert "requirement_gdpr_art_30" in ids
    assert "requirement_gdpr_art_32" in ids


def test_coverage_counts_only_approved(compliance_db):
    assess(compliance_db)
    assert coverage(compliance_db)["overall"] == 0.0  # nothing approved yet

    _approve_satisfied(compliance_db)
    data = coverage(compliance_db)
    assert data["overall"] == 0.5  # 1 of 2 GDPR requirements satisfied
    assert data["standards"][0]["satisfied"] == 1
    assert data["standards"][0]["total"] == 2


def test_approved_requirement_leaves_gaps(compliance_db):
    assess(compliance_db)
    _approve_satisfied(compliance_db)
    ids = {g["object_id"] for g in gaps(compliance_db)}
    assert "requirement_gdpr_art_32" not in ids  # now covered
    assert "requirement_gdpr_art_30" in ids       # still a gap


def test_prove_declines_without_approved_evidence(compliance_db):
    assess(compliance_db)
    result = prove(compliance_db, "Art. 32")
    assert result["found"] is True
    assert result["proven"] is False
    assert result["message"] == "No supporting evidence found."


def test_prove_succeeds_after_approval(compliance_db):
    assess(compliance_db)
    _approve_satisfied(compliance_db)
    result = prove(compliance_db, "Art. 32")
    assert result["proven"] is True
    assert result["assessments"]
    assert result["assessments"][0]["evidence"]


def test_prove_unknown_requirement_declines(compliance_db):
    result = prove(compliance_db, "nonexistent clause")
    assert result["found"] is False
    assert result["proven"] is False
