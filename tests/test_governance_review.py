"""Tests for the governance review workflow (Prompt #10)."""

from catalog.db import connect
from catalog.governance import repository as repo
from catalog.governance.config import load_governance_config
from catalog.governance.models import FreshnessState, ReviewWorkflowState
from catalog.governance.service import (
    approve_object,
    archive_object,
    flag_object,
    reject_object,
    run_scan,
)
from catalog.knowledge import repository as know_repo


def _life(db, oid):
    with connect(db) as conn:
        return repo.get_lifecycle(conn, oid)


def test_new_objects_start_pending(governed_db):
    with connect(governed_db) as conn:
        rows = repo.lifecycle_by_review(conn, (ReviewWorkflowState.PENDING_REVIEW.value,))
    assert rows  # everything starts in the review queue


def test_approve_sets_state_and_exports(governed_db):
    assert approve_object(governed_db, "capability_release_governance")
    life = _life(governed_db, "capability_release_governance")
    assert life["review_state"] == ReviewWorkflowState.APPROVED.value
    assert life["last_reviewed_at"] is not None
    assert life["last_confirmed_at"] is not None
    # Approval flows into the consolidation status so RDF export can see it.
    with connect(governed_db) as conn:
        obj = know_repo.get_object(conn, "capability_release_governance")
    assert obj["status"] == "APPROVED"


def test_approve_records_audit_trail(governed_db):
    approve_object(governed_db, "capability_release_governance", reviewer="alice", note="looks good")
    with connect(governed_db) as conn:
        reviews = conn.execute(
            "SELECT * FROM knowledge_reviews WHERE target_id = ?",
            ("capability_release_governance",),
        ).fetchall()
    assert any(r["action"] == "APPROVED" and r["reviewer"] == "alice" for r in reviews)


def test_archive_sets_archived_freshness(governed_db):
    assert archive_object(governed_db, "capability_release_governance")
    life = _life(governed_db, "capability_release_governance")
    assert life["review_state"] == ReviewWorkflowState.ARCHIVED.value
    assert life["freshness_state"] == FreshnessState.ARCHIVED.value


def test_flag_marks_needs_attention(governed_db):
    assert flag_object(governed_db, "capability_release_governance")
    life = _life(governed_db, "capability_release_governance")
    assert life["review_state"] == ReviewWorkflowState.NEEDS_ATTENTION.value


def test_reject_excludes_from_export(governed_db):
    assert reject_object(governed_db, "capability_release_governance")
    with connect(governed_db) as conn:
        obj = know_repo.get_object(conn, "capability_release_governance")
    assert obj["status"] == "REJECTED"


def test_review_unknown_object_returns_false(governed_db):
    assert approve_object(governed_db, "capability_nope") is False


def test_approve_survives_rescan(governed_db):
    approve_object(governed_db, "capability_release_governance")
    run_scan(governed_db, load_governance_config("config/governance.yml"))
    life = _life(governed_db, "capability_release_governance")
    assert life["review_state"] == ReviewWorkflowState.APPROVED.value


def test_review_queue_shrinks_after_approval(governed_db):
    with connect(governed_db) as conn:
        before = len(repo.lifecycle_by_review(conn, (ReviewWorkflowState.PENDING_REVIEW.value,)))
    approve_object(governed_db, "capability_release_governance")
    with connect(governed_db) as conn:
        after = len(repo.lifecycle_by_review(conn, (ReviewWorkflowState.PENDING_REVIEW.value,)))
    assert after == before - 1
