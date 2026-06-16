"""REST API tests for the compliance endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from catalog.api.app import create_app
from catalog.api.config import ApiSettings
from catalog.compliance.service import assess
from catalog.db import connect


@pytest.fixture
def comp_client(compliance_db, tmp_path) -> TestClient:
    assess(compliance_db)
    settings = ApiSettings(
        db_path=compliance_db,
        cache_dir=str(tmp_path / "cache"),
        compliance_config=str(tmp_path / "missing.yml"),
    )
    return TestClient(create_app(settings))


def test_list_standards(comp_client):
    resp = comp_client.get("/api/compliance/standards")
    assert resp.status_code == 200
    names = {s["name"] for s in resp.json()}
    assert "GDPR" in names


def test_list_requirements_paginated(comp_client):
    resp = comp_client.get("/api/compliance/requirements")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {r["clause_ref"] for r in body["items"]} == {"Art. 32", "Art. 30"}


def test_coverage_and_gaps(comp_client):
    cov = comp_client.get("/api/compliance/coverage").json()
    assert cov["overall"] == 0.0  # nothing approved yet

    gaps = comp_client.get("/api/compliance/gaps").json()
    assert {g["object_id"] for g in gaps} == {
        "requirement_gdpr_art_32",
        "requirement_gdpr_art_30",
    }


def test_approve_assessment_changes_coverage(comp_client, compliance_db):
    with connect(compliance_db) as conn:
        a = conn.execute(
            "SELECT id FROM compliance_assessments WHERE status='SATISFIED'"
        ).fetchone()
    resp = comp_client.post(f"/api/compliance/assessments/{a['id']}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "APPROVED"

    cov = comp_client.get("/api/compliance/coverage").json()
    assert cov["overall"] == 0.5


def test_prove_endpoint_declines_without_approval(comp_client):
    resp = comp_client.get("/api/compliance/prove/Art. 32")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["proven"] is False
    assert body["message"] == "No supporting evidence found."


def test_assess_job_endpoint(comp_client):
    resp = comp_client.post("/api/compliance/assess")
    assert resp.status_code == 200
    assert resp.json()["job_type"] == "compliance-assess"


def test_missing_assessment_returns_404(comp_client):
    resp = comp_client.post("/api/compliance/assessments/99999/approve")
    assert resp.status_code == 404


def test_unknown_standard_returns_404(comp_client):
    resp = comp_client.get("/api/compliance/standards/standard_unknown")
    assert resp.status_code == 404
