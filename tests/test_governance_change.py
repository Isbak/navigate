"""Tests for governance change tracking and evolution (Prompt #10)."""

from catalog.db import connect, init_db
from catalog.governance import repository as repo
from catalog.governance.config import load_governance_config
from catalog.governance.service import run_scan
from catalog.knowledge.service import consolidate

CONFIG = load_governance_config("config/governance.yml")


def _changes(db, change_type=None):
    with connect(db) as conn:
        if change_type:
            return conn.execute(
                "SELECT * FROM knowledge_change_log WHERE change_type = ? ORDER BY id",
                (change_type,),
            ).fetchall()
        return repo.all_changes(conn)


def _cap(conn, artifact, name, confidence=0.9):
    conn.execute(
        "INSERT INTO candidate_capabilities(artifact_id, name, confidence, "
        "supporting_text, knowledge_type, review_status, model, created_at) "
        "VALUES (?, ?, ?, 'ev', 'OBSERVATION', 'NEW', 'stub', 't')",
        (artifact, name, confidence),
    )


def _rel(conn, artifact, s, p, o):
    conn.execute(
        "INSERT INTO candidate_relationships(artifact_id, subject, predicate, object, "
        "confidence, supporting_text, review_status, model, created_at) "
        "VALUES (?, ?, ?, ?, 0.9, 'ev', 'NEW', 'stub', 't')",
        (artifact, s, p, o),
    )


def test_first_scan_logs_object_added(governed_db):
    added = _changes(governed_db, "object_added")
    assert added
    ids = {c["object_id"] for c in added}
    assert "capability_release_governance" in ids


def test_first_scan_logs_relationship_added(governed_db):
    added = _changes(governed_db, "relationship_added")
    assert added


def test_new_object_detected_on_rescan(governed_db):
    with connect(governed_db) as conn:
        before = len(_changes(governed_db, "object_added"))
        _cap(conn, "doc_c", "Brand New Capability")
        conn.commit()
    consolidate(governed_db)
    run_scan(governed_db, CONFIG)
    after = _changes(governed_db, "object_added")
    assert len(after) > before
    assert any(c["object_id"] == "capability_brand_new_capability" for c in after)


def test_removed_object_detected_on_rescan(governed_db):
    # Drop everything from one document and re-consolidate so an object vanishes.
    with connect(governed_db) as conn:
        conn.execute("DELETE FROM candidate_capabilities WHERE artifact_id = 'doc_b'")
        conn.commit()
    consolidate(governed_db)
    run_scan(governed_db, CONFIG)
    removed = _changes(governed_db, "object_removed")
    assert any(c["object_id"] == "capability_release_management" for c in removed)


def test_confidence_change_is_tracked(tmp_path):
    db = str(tmp_path / "c.sqlite")
    init_db(db)
    # One weak mention -> low confidence.
    with connect(db) as conn:
        _cap(conn, "doc_a", "Release Governance", confidence=0.5)
        conn.commit()
    consolidate(db)
    run_scan(db, CONFIG)

    # Many strong mentions across documents -> confidence climbs.
    with connect(db) as conn:
        for i in range(8):
            _cap(conn, f"doc_{i}", "Release Governance", confidence=0.95)
        conn.commit()
    consolidate(db)
    run_scan(db, CONFIG)

    changes = _changes(db, "confidence_changed")
    assert changes
    last = changes[-1]
    assert float(last["new_value"]) > float(last["old_value"])


def test_relationship_removed_detected(tmp_path):
    db = str(tmp_path / "c.sqlite")
    init_db(db)
    with connect(db) as conn:
        _cap(conn, "doc_a", "Release Governance")
        _cap(conn, "doc_a", "Release Management")
        _rel(conn, "doc_a", "Release Governance", "supports", "Release Management")
        conn.commit()
    consolidate(db)
    run_scan(db, CONFIG)
    assert _changes(db, "relationship_added")

    with connect(db) as conn:
        conn.execute("DELETE FROM candidate_relationships")
        conn.commit()
    consolidate(db)
    run_scan(db, CONFIG)
    assert _changes(db, "relationship_removed")


def test_history_is_chronological(governed_db):
    from catalog.governance.service import approve_object

    approve_object(governed_db, "capability_release_governance")
    with connect(governed_db) as conn:
        changes = repo.changes_for_object(conn, "capability_release_governance")
    ids = [c["id"] for c in changes]
    assert ids == sorted(ids)
    assert changes[-1]["change_type"] == "review_changed"
