"""Tests for governance drift detection (Prompt #10)."""

from catalog.db import connect
from catalog.governance.config import DriftConfig
from catalog.governance.drift import ObjectSnapshot, detect_drift
from catalog.governance.service import run_scan
from catalog.knowledge.service import consolidate

CONFIG = DriftConfig(evidence_drop_ratio=0.5, terminology_min_documents=5)


def _snap(oid, name, otype, docs):
    return ObjectSnapshot(object_id=oid, name=name, object_type=otype, document_count=docs)


def test_no_drift_when_unchanged():
    snap = {"a": _snap("a", "A", "Capability", 10)}
    assert detect_drift(snap, dict(snap), CONFIG) == []


def test_established_object_removed_is_flagged():
    prev = {"x": _snap("x", "Launchpad Model", "Decision", 30)}
    findings = detect_drift(prev, {}, CONFIG)
    kinds = {f.kind for f in findings}
    assert "removed" in kinds
    assert any("Launchpad Model" in f.message for f in findings)


def test_unestablished_removal_is_not_flagged():
    prev = {"x": _snap("x", "Minor Note", "Decision", 1)}
    assert detect_drift(prev, {}, CONFIG) == []


def test_terminology_change_pairs_removed_with_added():
    # The spec example: Launchpad Model (30 docs) replaced by Mission Delivery Model.
    prev = {"old": _snap("old", "Launchpad Model", "Decision", 30)}
    cur = {"new": _snap("new", "Mission Delivery Model", "Decision", 12)}
    findings = detect_drift(prev, cur, CONFIG)
    terminology = [f for f in findings if f.kind == "terminology_change"]
    assert terminology
    assert terminology[0].related_id == "new"
    assert "Mission Delivery Model" in terminology[0].message


def test_disappearing_evidence_when_support_collapses():
    prev = {"a": _snap("a", "A", "Capability", 20)}
    cur = {"a": _snap("a", "A", "Capability", 5)}
    findings = detect_drift(prev, cur, CONFIG)
    assert any(f.kind == "disappearing_evidence" for f in findings)


def test_small_evidence_drop_is_not_drift():
    prev = {"a": _snap("a", "A", "Capability", 20)}
    cur = {"a": _snap("a", "A", "Capability", 18)}
    assert detect_drift(prev, cur, CONFIG) == []


# -- scan-level drift over the database ---------------------------------------

def _seed_decision(conn, artifact, text):
    conn.execute(
        "INSERT INTO candidate_decisions(artifact_id, decision_text, confidence, "
        "supporting_text, knowledge_type, review_status, model, created_at) "
        "VALUES (?, ?, 0.9, 'evidence', 'OBSERVATION', 'NEW', 'stub', 't')",
        (artifact, text),
    )


def test_scan_detects_terminology_drift(tmp_path):
    from catalog.db import init_db

    db = str(tmp_path / "c.sqlite")
    init_db(db)
    # Launchpad Model established across many documents.
    with connect(db) as conn:
        for i in range(8):
            _seed_decision(conn, f"doc_{i}", "Launchpad Model")
        conn.commit()
    consolidate(db)
    run_scan(db)  # captures the snapshot with Launchpad present in 8 docs

    # The term is replaced wholesale by Mission Delivery Model.
    with connect(db) as conn:
        conn.execute("DELETE FROM candidate_decisions")
        for i in range(8):
            _seed_decision(conn, f"doc_{i}", "Mission Delivery Model")
        conn.commit()
    consolidate(db)
    stats = run_scan(db)

    assert stats.drift_findings >= 1
    with connect(db) as conn:
        drift = conn.execute(
            "SELECT * FROM knowledge_change_log WHERE change_type = 'drift_detected'"
        ).fetchall()
    messages = " ".join(r["detail"] for r in drift)
    assert "Launchpad Model" in messages
    assert "Mission Delivery Model" in messages
