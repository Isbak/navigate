"""Tests for governance alert generation (Prompt #10)."""

from catalog.db import connect
from catalog.governance import repository as repo
from catalog.governance.config import FreshnessConfig, GovernanceConfig, load_governance_config
from catalog.governance.models import AlertType, FreshnessState, ReviewWorkflowState
from catalog.governance.ownership import assign_owner
from catalog.governance.service import run_scan

CONFIG = load_governance_config("config/governance.yml")


def _alert_types(db):
    with connect(db) as conn:
        return {r["alert_type"] for r in repo.open_alerts(conn)}


def test_missing_owner_alert(governed_db):
    assert AlertType.MISSING_OWNER.value in _alert_types(governed_db)


def test_missing_owner_alert_clears_after_ownership(governed_db):
    # Assign owners to every object, then rescan: missing-owner alerts disappear.
    with connect(governed_db) as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_objects")]
    for oid in ids:
        assign_owner(governed_db, oid, "Team", "Some Team")
    run_scan(governed_db, CONFIG)
    assert AlertType.MISSING_OWNER.value not in _alert_types(governed_db)


def test_alerts_are_regenerated_not_duplicated(governed_db):
    with connect(governed_db) as conn:
        first = len(repo.open_alerts(conn))
    run_scan(governed_db, CONFIG)
    with connect(governed_db) as conn:
        second = len(repo.open_alerts(conn))
    assert first == second  # a re-scan replaces alerts, it does not pile them up


def test_stale_knowledge_alert(governed_db):
    # Backdate one object's last-seen so the next scan ages it past STALE, then
    # remove it from the graph so it is no longer "seen".
    with connect(governed_db) as conn:
        conn.execute(
            "UPDATE knowledge_lifecycle SET last_seen_at = '2020-01-01T00:00:00+00:00' "
            "WHERE object_id = 'capability_release_management'"
        )
        # Drop its underlying object so the scan treats it as absent and ages it.
        conn.execute("DELETE FROM knowledge_objects WHERE id = 'capability_release_management'")
        conn.commit()
    run_scan(governed_db, CONFIG)
    with connect(governed_db) as conn:
        life = repo.get_lifecycle(conn, "capability_release_management")
        types = {r["alert_type"] for r in repo.open_alerts(conn)}
    assert life["freshness_state"] in (FreshnessState.STALE.value, FreshnessState.ARCHIVED.value)
    assert AlertType.STALE_KNOWLEDGE.value in types


def test_stale_review_alert():
    # An approved object reviewed long ago should raise a stale-review alert.
    import tempfile, os
    from catalog.db import init_db
    from catalog.knowledge.service import consolidate
    from catalog.governance.service import approve_object

    d = tempfile.mkdtemp()
    db = os.path.join(d, "c.sqlite")
    init_db(db)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO candidate_capabilities(artifact_id, name, confidence, "
            "supporting_text, knowledge_type, review_status, model, created_at) "
            "VALUES('doc_a','Release Governance',0.9,'ev','OBSERVATION','NEW','stub','t')"
        )
        conn.commit()
    consolidate(db)
    run_scan(db, CONFIG)
    approve_object(db, "capability_release_governance")
    # Backdate the review far into the past.
    with connect(db) as conn:
        conn.execute(
            "UPDATE knowledge_lifecycle SET last_reviewed_at = '2020-01-01T00:00:00+00:00' "
            "WHERE object_id = 'capability_release_governance'"
        )
        conn.commit()
    run_scan(db, CONFIG)
    with connect(db) as conn:
        types = {r["alert_type"] for r in repo.open_alerts(conn)}
    assert AlertType.STALE_REVIEW.value in types


def test_orphaned_object_alert(tmp_path):
    # An object with no relationships is an orphan.
    from catalog.db import init_db
    from catalog.knowledge.service import consolidate

    db = str(tmp_path / "c.sqlite")
    init_db(db)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO candidate_capabilities(artifact_id, name, confidence, "
            "supporting_text, knowledge_type, review_status, model, created_at) "
            "VALUES('doc_a','Lonely Capability',0.9,'ev','OBSERVATION','NEW','stub','t')"
        )
        conn.commit()
    consolidate(db)
    run_scan(db, CONFIG)
    assert AlertType.ORPHANED_OBJECT.value in _alert_types(db)
