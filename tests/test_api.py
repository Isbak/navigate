"""Tests for the Navigate REST API.

Each test runs against an isolated, fully seeded SQLite database: a small
consolidated, approved, and governed knowledge graph plus a couple of indexed
artifacts and links. The API is exercised through FastAPI's TestClient (httpx).
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from catalog.api.app import create_app
from catalog.api.config import ApiSettings
from catalog.db import connect, init_db
from catalog.governance.config import load_governance_config
from catalog.governance.service import run_scan
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

    conn.execute(
        "INSERT INTO document_classifications(artifact_id, document_type, type_confidence, "
        "domains, short_summary, long_summary, model, created_at) "
        "VALUES('doc_a','strategy',0.9,?,'s','l','stub','t')",
        ('[{"domain": "Test & Release", "confidence": 0.9}]',),
    )


def _seed_artifacts_and_links(conn) -> None:
    for art_id, filename, file_type in (
        ("doc_a", "governance.pdf", "pdf"),
        ("doc_b", "release.docx", "docx"),
    ):
        conn.execute(
            "INSERT INTO artifacts(path, id, filename, file_type, size_bytes, "
            "source_system, scan_status, first_seen_at, last_scanned_at) "
            "VALUES (?, ?, ?, ?, 1024, 'local_laptop', 'RAW', 't', 't')",
            (f"/docs/{filename}", art_id, filename, file_type),
        )
    conn.execute(
        "INSERT INTO links(source_artifact_id, raw_url, normalized_url, anchor_text, "
        "target_system, target_type, link_kind, discovered_at, last_seen_at, status) "
        "VALUES ('doc_a', 'https://x.com/a', 'https://x.com/a', 'A', "
        "'sharepoint', 'document', 'external', 't', 't', 'ACTIVE')"
    )
    conn.execute(
        "INSERT INTO links(source_artifact_id, raw_url, normalized_url, anchor_text, "
        "target_system, target_type, link_kind, discovered_at, last_seen_at, status) "
        "VALUES ('doc_b', 'https://x.com/b', 'https://x.com/b', 'B', "
        "'confluence', 'page', 'external', 't', 't', 'ACTIVE')"
    )


@pytest.fixture
def seeded_db(tmp_path) -> str:
    db = str(tmp_path / "catalog.sqlite")
    init_db(db)
    with connect(db) as conn:
        _seed_candidates(conn)
        _seed_artifacts_and_links(conn)
        conn.commit()
    consolidate(db)
    with connect(db) as conn:
        object_ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_objects")]
        rel_ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_relationships")]
    for oid in object_ids:
        review_object(db, oid, ReviewState.APPROVED.value)
    for rid in rel_ids:
        review_relationship(db, rid, ReviewState.APPROVED.value)
    run_scan(db, load_governance_config("config/governance.yml"))
    return db


def _settings(db: str, tmp_path, **overrides) -> ApiSettings:
    return ApiSettings(
        db_path=db,
        cache_dir=str(tmp_path / "cache"),
        **overrides,
    )


@pytest.fixture
def client(seeded_db, tmp_path) -> TestClient:
    return TestClient(create_app(_settings(seeded_db, tmp_path)))


# -- Swagger / OpenAPI ---------------------------------------------------------

def test_swagger_docs_and_openapi_schema(client):
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "Swagger UI" in docs.text

    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Navigate API"
    assert schema["openapi"].startswith("3.")
    assert {tag["name"] for tag in schema["tags"]} >= {
        "artifacts",
        "knowledge",
        "jobs",
    }
    assert "/api/artifacts" in schema["paths"]
    assert schema["servers"] == [
        {"url": "/", "description": "Navigate API application root"}
    ]
    assert "HTTPBearer" in schema["components"]["securitySchemes"]


# -- health & stats -----------------------------------------------------------

def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"]["connected"] is True
    assert "version" in body


def test_stats(client):
    body = client.get("/api/stats").json()
    assert body["artifact_count"] == 2
    assert body["link_count"] == 2
    assert body["knowledge_object_count"] >= 1
    assert body["relationship_count"] >= 1
    assert body["evidence_count"] >= 1
    for key in ("pending_review_count", "stale_object_count"):
        assert key in body


# -- artifacts ----------------------------------------------------------------

def test_artifact_list_and_detail(client):
    body = client.get("/api/artifacts").json()
    assert body["total"] == 2
    assert {a["id"] for a in body["items"]} == {"doc_a", "doc_b"}
    detail = client.get("/api/artifacts/doc_a").json()
    assert detail["id"] == "doc_a"
    assert detail["classification_status"] == "CLASSIFIED"
    assert detail["extraction_status"] == "PENDING"


def test_artifact_filtering(client):
    body = client.get("/api/artifacts", params={"file_type": "pdf"}).json()
    assert body["total"] == 1
    assert body["items"][0]["file_type"] == "pdf"

    classified = client.get(
        "/api/artifacts", params={"classification_status": "CLASSIFIED"}
    ).json()
    assert {a["id"] for a in classified["items"]} == {"doc_a"}


def test_artifact_pagination(client):
    page = client.get("/api/artifacts", params={"limit": 1, "offset": 0}).json()
    assert page["limit"] == 1
    assert page["offset"] == 0
    assert page["total"] == 2
    assert len(page["items"]) == 1


def test_artifact_links_and_evidence(client):
    links = client.get("/api/artifacts/doc_a/links").json()
    assert links["total"] == 1
    assert links["items"][0]["target_system"] == "sharepoint"
    evidence = client.get("/api/artifacts/doc_a/evidence").json()
    assert evidence["total"] >= 1


# -- links --------------------------------------------------------------------

def test_links_list_and_filter(client):
    body = client.get("/api/links").json()
    assert body["total"] == 2
    filtered = client.get("/api/links", params={"target_system": "confluence"}).json()
    assert filtered["total"] == 1


def test_link_stats_and_top_targets(client):
    stats = client.get("/api/links/stats").json()
    assert stats["total"] == 2
    assert any(c["key"] == "sharepoint" for c in stats["by_target_system"])
    top = client.get("/api/links/top-targets").json()
    assert len(top) == 2


# -- knowledge ----------------------------------------------------------------

def test_knowledge_list_and_detail(client):
    body = client.get("/api/knowledge-objects").json()
    assert body["total"] >= 1
    obj = body["items"][0]
    detail = client.get(f"/api/knowledge-objects/{obj['id']}").json()
    assert detail["id"] == obj["id"]
    assert detail["review_status"] is not None  # joined from governance lifecycle


def test_knowledge_filter_min_confidence(client):
    body = client.get(
        "/api/knowledge-objects", params={"min_confidence": 0.99}
    ).json()
    assert all(o["confidence"] >= 0.99 for o in body["items"])


def test_knowledge_relationships_and_mentions(client):
    obj_id = client.get("/api/knowledge-objects").json()["items"][0]["id"]
    rels = client.get(f"/api/knowledge-objects/{obj_id}/relationships").json()
    assert "items" in rels
    mentions = client.get(f"/api/knowledge-objects/{obj_id}/mentions").json()
    assert "items" in mentions


# -- relationships ------------------------------------------------------------

def test_relationship_list_detail_and_approval(client):
    body = client.get("/api/relationships").json()
    assert body["total"] >= 1
    rel_id = body["items"][0]["id"]
    detail = client.get(f"/api/relationships/{rel_id}").json()
    assert detail["id"] == rel_id

    resp = client.post(f"/api/relationships/{rel_id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"
    assert client.get(f"/api/relationships/{rel_id}").json()["review_status"] == "REJECTED"


# -- governance ---------------------------------------------------------------

def test_governance_dashboard(client):
    body = client.get("/api/governance/dashboard").json()
    assert "knowledge_objects" in body
    assert "average_quality" in body


def test_governance_quality_and_orphaned(client):
    quality = client.get("/api/governance/quality").json()
    assert "average_quality" in quality
    assert "items" in quality
    orphaned = client.get("/api/governance/orphaned").json()
    assert "objects_without_owner" in orphaned


# -- graph --------------------------------------------------------------------

def test_graph_nodes_and_edges(client):
    nodes = client.get("/api/graph/nodes").json()
    assert nodes["total"] >= 1
    edges = client.get("/api/graph/edges").json()
    assert "items" in edges


def test_graph_neighbors(client):
    obj_id = client.get("/api/knowledge-objects").json()["items"][0]["id"]
    resp = client.get(f"/api/graph/object/{obj_id}/neighbors")
    assert resp.status_code == 200
    assert resp.json()["object_id"] == obj_id


def test_graph_export_json(client):
    body = client.get("/api/graph/export-json").json()
    assert "nodes" in body and "edges" in body


# -- ask ----------------------------------------------------------------------

def test_ask_not_implemented_by_default(client):
    resp = client.post("/api/ask", json={"question": "what is release governance?"})
    assert resp.status_code == 501
    assert resp.json()["error"] == "not_implemented"
    assert "not implemented" in resp.json()["message"].lower()


# -- jobs ---------------------------------------------------------------------

def test_jobs_consolidate_and_list(client):
    resp = client.post("/api/jobs/consolidate")
    assert resp.status_code == 200
    job = resp.json()
    assert job["status"] == "COMPLETED"
    assert job["job_type"] == "consolidate"

    listing = client.get("/api/jobs").json()
    assert listing["total"] >= 1
    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert detail["id"] == job["id"]


def test_jobs_classify_disabled_fails_cleanly(client):
    resp = client.post("/api/jobs/classify")
    assert resp.status_code == 200  # the job is created and recorded
    assert resp.json()["status"] == "FAILED"
    assert "disabled" in resp.json()["error_message"].lower()


# -- error handling -----------------------------------------------------------

def test_not_found_error_shape(client):
    resp = client.get("/api/artifacts/missing")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "not_found"
    assert body["message"]
    assert isinstance(body["details"], dict)


def test_validation_error(client):
    resp = client.get("/api/artifacts", params={"limit": 0})
    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_error"


# -- API key auth -------------------------------------------------------------

def test_api_key_auth(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIGATE_API_KEY", "secret-token")
    settings = _settings(
        seeded_db, tmp_path, require_api_key=True, api_key_env="NAVIGATE_API_KEY"
    )
    client = TestClient(create_app(settings))

    assert client.get("/api/health").status_code == 401
    ok = client.get("/api/health", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200
    bad = client.get("/api/health", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401
