"""Tests for policy-bounded agent approval and the human undo (revert)."""

from catalog.db import connect
from catalog.governance import service as gov_service
from catalog.governance.agent_review import (
    agent_approve,
    revert_agent_actions,
    revert_review,
)
from catalog.governance.config import AgentReviewConfig
from catalog.governance.models import ReviewWorkflowState
from catalog.knowledge import repository as know_repo

CAPABILITY = "capability_release_governance"


def _policy(**overrides) -> AgentReviewConfig:
    base = {
        "enabled": True,
        "agent_name": "tester",
        "min_confidence": 0.5,
        "max_confidence": 1.0,
        "require_evidence": True,
    }
    base.update(overrides)
    return AgentReviewConfig(**base)


def _object_status(db, object_id):
    with connect(db) as conn:
        return know_repo.get_object(conn, object_id)["status"]


def _review_state(db, object_id):
    from catalog.governance import repository as gov_repo

    with connect(db) as conn:
        return gov_repo.get_lifecycle(conn, object_id)["review_state"]


def _relationship_status(db, rel_id):
    with connect(db) as conn:
        return know_repo.get_relationship(conn, rel_id)["review_status"]


# -- agent_approve ------------------------------------------------------------

def test_agent_approve_tags_agent_and_approves(governed_db):
    stats = agent_approve(governed_db, config=_policy())
    assert stats.reviewer == "agent:tester"
    assert stats.objects_approved == 5
    assert stats.relationships_approved == 5
    assert _object_status(governed_db, CAPABILITY) == "APPROVED"
    assert _review_state(governed_db, CAPABILITY) == ReviewWorkflowState.APPROVED.value
    # The decision is attributed to the agent in the audit trail.
    with connect(governed_db) as conn:
        reviews = conn.execute(
            "SELECT reviewer FROM knowledge_reviews WHERE target_id = ?", (CAPABILITY,)
        ).fetchall()
    assert any(r["reviewer"] == "agent:tester" for r in reviews)


def test_agent_approve_dry_run_writes_nothing(governed_db):
    stats = agent_approve(governed_db, config=_policy(), dry_run=True)
    assert stats.dry_run is True
    assert stats.objects_approved == 0 and stats.relationships_approved == 0
    assert stats.candidates  # but it reports what it would have done
    assert _object_status(governed_db, CAPABILITY) == "PROPOSED"


def test_confidence_window_excludes_low_confidence_objects(governed_db):
    # Objects score ~0.55-0.62; the default 0.85 floor leaves only relationships.
    stats = agent_approve(governed_db, config=_policy(min_confidence=0.85))
    assert stats.objects_approved == 0
    assert stats.relationships_approved == 5


def test_object_type_allowlist(governed_db):
    stats = agent_approve(
        governed_db,
        config=_policy(allowed_object_types=("Capability",)),
        target="objects",
    )
    assert stats.objects_approved == 2  # the two capabilities only


def test_predicate_allowlist(governed_db):
    stats = agent_approve(
        governed_db,
        config=_policy(allowed_predicates=("supports",)),
        target="relationships",
    )
    assert stats.relationships_approved == 2  # ids 1 and 4


def test_max_per_run_caps_a_pass(governed_db):
    stats = agent_approve(governed_db, config=_policy(max_per_run=2))
    assert stats.objects_approved == 2
    assert stats.relationships_approved == 0
    assert stats.objects_skipped >= 3


def test_require_evidence_can_be_relaxed(governed_db):
    # Sanity: all seeded items have evidence, so toggling the flag is a no-op here
    # but proves the flag is honoured rather than ignored.
    stats = agent_approve(governed_db, config=_policy(require_evidence=False))
    assert stats.total_approved == 10


# -- revert -------------------------------------------------------------------

def test_revert_object_restores_pending_and_unexports(governed_db):
    agent_approve(governed_db, config=_policy(), target="objects")
    assert _object_status(governed_db, CAPABILITY) == "APPROVED"

    result = revert_review(governed_db, "object", CAPABILITY, reviewer="alice")
    assert result.reverted
    assert result.from_state == ReviewWorkflowState.APPROVED.value
    assert result.to_state == ReviewWorkflowState.PENDING_REVIEW.value
    # Reverting an approval must also un-export the object.
    assert _object_status(governed_db, CAPABILITY) == "PROPOSED"
    assert _review_state(governed_db, CAPABILITY) == ReviewWorkflowState.PENDING_REVIEW.value


def test_revert_relationship_restores_proposed(governed_db):
    agent_approve(governed_db, config=_policy(), target="relationships")
    assert _relationship_status(governed_db, 1) == "APPROVED"

    result = revert_review(governed_db, "relationship", "1", reviewer="alice")
    assert result.reverted
    assert result.to_state == "PROPOSED"
    assert _relationship_status(governed_db, 1) == "PROPOSED"


def test_revert_records_human_attributed_audit_event(governed_db):
    agent_approve(governed_db, config=_policy(), target="objects")
    revert_review(governed_db, "object", CAPABILITY, reviewer="alice", note="undo")
    with connect(governed_db) as conn:
        last = conn.execute(
            "SELECT reviewer, action FROM knowledge_reviews WHERE target_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (CAPABILITY,),
        ).fetchone()
    assert last["reviewer"] == "alice"


def test_revert_nothing_when_no_history(governed_db):
    result = revert_review(governed_db, "object", CAPABILITY)
    assert not result.reverted
    assert "no review history" in result.reason


# -- revert_agent_actions -----------------------------------------------------

def test_revert_agent_rolls_back_a_batch(governed_db):
    agent_approve(governed_db, config=_policy())
    stats = revert_agent_actions(governed_db, agent="tester", reviewer="alice")
    assert stats.reverted == 10
    assert _object_status(governed_db, CAPABILITY) == "PROPOSED"
    assert _relationship_status(governed_db, 1) == "PROPOSED"


def test_revert_agent_never_clobbers_a_later_human_decision(governed_db):
    agent_approve(governed_db, config=_policy(), target="objects")
    # A human re-approves one object after the agent did.
    gov_service.approve_object(governed_db, CAPABILITY, reviewer="alice")

    stats = revert_agent_actions(governed_db, agent="tester", reviewer="bob")
    # The human-held object is skipped; the others are reverted.
    assert _object_status(governed_db, CAPABILITY) == "APPROVED"
    assert stats.skipped >= 1
    assert _object_status(governed_db, "platform_salesforce") == "PROPOSED"


def test_revert_agent_scopes_to_one_agent(governed_db):
    agent_approve(governed_db, config=_policy(agent_name="alpha"), target="objects")
    # A different agent should not be touched when scoping to "alpha".
    stats = revert_agent_actions(governed_db, agent="beta", reviewer="alice")
    assert stats.reverted == 0
    assert _object_status(governed_db, CAPABILITY) == "APPROVED"


# -- MCP write tools ----------------------------------------------------------

def _mcp_settings(db, tmp_path, **policy):
    from catalog.mcp.config import McpSettings

    cfg = tmp_path / "governance.yml"
    lines = ["agent_review:", "  enabled: true", "  agent_name: mcpbot"]
    for key, value in {"min_confidence": 0.5, "max_confidence": 1.0, **policy}.items():
        lines.append(f"  {key}: {value}")
    cfg.write_text("\n".join(lines), encoding="utf-8")
    return McpSettings(
        db_path=db, governance_config=str(cfg), enable_agent_review=True
    )


def test_mcp_approve_object_in_policy(governed_db, tmp_path):
    from catalog.mcp import tools

    settings = _mcp_settings(governed_db, tmp_path)
    result = tools.approve_object(settings, CAPABILITY)
    assert result["approved"] is True
    assert result["reviewer"] == "agent:mcpbot"
    assert _object_status(governed_db, CAPABILITY) == "APPROVED"


def test_mcp_approve_object_out_of_policy_declines(governed_db, tmp_path):
    from catalog.mcp import tools

    settings = _mcp_settings(governed_db, tmp_path, min_confidence=0.95)
    result = tools.approve_object(settings, CAPABILITY)
    assert result["approved"] is False
    assert "confidence" in result["reason"]
    assert _object_status(governed_db, CAPABILITY) == "PROPOSED"


def test_mcp_write_disabled_by_default(governed_db, tmp_path):
    from catalog.mcp import tools
    from catalog.mcp.config import McpSettings

    cfg = tmp_path / "governance.yml"
    cfg.write_text("agent_review:\n  enabled: true\n  min_confidence: 0.5\n", encoding="utf-8")
    settings = McpSettings(db_path=governed_db, governance_config=str(cfg))
    result = tools.approve_object(settings, CAPABILITY)
    assert result["approved"] is False
    assert "disabled" in result["reason"]


def test_mcp_approve_relationship_in_policy(governed_db, tmp_path):
    from catalog.mcp import tools

    settings = _mcp_settings(governed_db, tmp_path)
    result = tools.approve_relationship(settings, 1)
    assert result["approved"] is True
    assert _relationship_status(governed_db, 1) == "APPROVED"


def test_mcp_flag_object_escalates(governed_db, tmp_path):
    from catalog.mcp import tools

    settings = _mcp_settings(governed_db, tmp_path)
    result = tools.flag_object(settings, CAPABILITY, note="unsure")
    assert result["flagged"] is True
    assert _review_state(governed_db, CAPABILITY) == ReviewWorkflowState.NEEDS_ATTENTION.value


# -- REST API -----------------------------------------------------------------

def _api_client(db):
    from fastapi.testclient import TestClient

    from catalog.api.app import create_app
    from catalog.api.config import ApiSettings

    return TestClient(create_app(ApiSettings(db_path=db)))


def test_api_agent_approve_and_revert_roundtrip(governed_db):
    client = _api_client(governed_db)

    # Relationships score 0.90, inside the default 0.85 window.
    resp = client.post(
        "/api/governance/agent-approve",
        json={"target": "relationships", "note": "api pass"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reviewer"] == "agent:agent"
    assert body["relationships_approved"] == 5
    assert _relationship_status(governed_db, 1) == "APPROVED"

    # And the batch is reversible.
    undo = client.post("/api/governance/revert-agent", json={})
    assert undo.status_code == 200
    assert undo.json()["reverted"] == 5
    assert _relationship_status(governed_db, 1) == "PROPOSED"


def test_api_agent_approve_dry_run_writes_nothing(governed_db):
    client = _api_client(governed_db)
    resp = client.post(
        "/api/governance/agent-approve",
        json={"target": "relationships", "dry_run": True},
    )
    assert resp.status_code == 200
    assert resp.json()["relationships_approved"] == 0
    assert _relationship_status(governed_db, 1) == "PROPOSED"
