import json

from catalog.db import connect
from catalog.semantic import analytics
from catalog.semantic import repository as repo
from catalog.semantic.providers.base import BaseLLMProvider
from catalog.semantic.service import classify_documents


class StubProvider(BaseLLMProvider):
    """Returns a canned response per artifact and counts generate() calls."""

    def __init__(self, model="stub-model", responses=None, default=None):
        super().__init__(model)
        self.responses = responses or {}
        self.default = default or {"document_type": "Report", "type_confidence": 0.5}
        self.calls = 0
        self.seen_artifacts = []

    def generate(self, prompt, *, system=None):
        self.calls += 1
        # The metadata filename is embedded in the prompt; match on it.
        for key, payload in self.responses.items():
            if key in prompt:
                self.seen_artifacts.append(key)
                return json.dumps(payload)
        return json.dumps(self.default)


def _write_cache(cache_dir, artifact_id, text, filename=None):
    d = cache_dir / artifact_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "extracted.txt").write_text(text, encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps({"artifact_id": artifact_id, "filename": filename or artifact_id}),
        encoding="utf-8",
    )


GOVERNANCE = {
    "document_type": "Governance",
    "type_confidence": 0.93,
    "short_summary": "Release governance.",
    "long_summary": "A longer summary.",
    "domains": [
        {"domain": "Test & Release", "confidence": 0.9},
        {"domain": "Digital Transformation", "confidence": 0.7},
    ],
    "capabilities": [{"name": "Release Management", "confidence": 0.92}],
    "entities": [
        {"entity_type": "Technology", "name": "SAP", "confidence": 0.8},
        {"entity_type": "Concept", "name": "Launchpad Model", "confidence": 0.85},
    ],
    "decisions": [{"decision_text": "Use Launchpad model", "confidence": 0.84}],
    "risks": [{"risk_description": "Unclear ownership", "confidence": 0.7}],
    "relationships": [
        {"subject": "Release Governance", "predicate": "supports",
         "object": "Launchpad Model", "confidence": 0.87},
    ],
}


def test_persists_classification_and_candidates(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_gov", "release governance", filename="gov.pptx")

    provider = StubProvider(responses={"gov.pptx": GOVERNANCE})
    stats = classify_documents(db, cache, provider)

    assert stats.documents_processed == 1
    assert stats.decisions == 1
    assert stats.relationships == 1

    with connect(db) as conn:
        row = repo.get_classification(conn, "doc_gov")
        assert row["document_type"] == "Governance"
        assert row["type_confidence"] == 0.93
        # Provenance + storage tiering.
        assert row["model"] == "stub-model"
        assert row["knowledge_type"] == "OBSERVATION"
        assert row["review_status"] == "NEW"
        assert row["created_at"]

        caps = repo.capabilities(conn)
        assert caps[0]["name"] == "Release Management"

        decs = repo.decisions(conn)
        assert decs[0]["knowledge_type"] == "HYPOTHESIS"
        assert decs[0]["review_status"] == "NEW"

        rels = repo.relationships(conn)
        assert rels[0]["predicate"] == "supports"
        assert rels[0]["knowledge_type"] == "HYPOTHESIS"


def test_incremental_skips_unchanged(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", "content", filename="a.txt")

    provider = StubProvider()
    first = classify_documents(db, cache, provider)
    assert first.documents_processed == 1
    assert provider.calls == 1

    # Same extraction -> skipped, no second LLM call.
    second = classify_documents(db, cache, provider)
    assert second.documents_processed == 0
    assert second.documents_skipped == 1
    assert provider.calls == 1


def test_incremental_reclassifies_when_extraction_changes(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", "content v1", filename="a.txt")

    provider = StubProvider()
    classify_documents(db, cache, provider)
    assert provider.calls == 1

    _write_cache(cache, "doc_a", "content v2 changed", filename="a.txt")
    stats = classify_documents(db, cache, provider)
    assert stats.documents_processed == 1
    assert provider.calls == 2


def test_force_reclassifies(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", "content", filename="a.txt")

    provider = StubProvider()
    classify_documents(db, cache, provider)
    stats = classify_documents(db, cache, provider, force=True)
    assert stats.documents_processed == 1
    assert provider.calls == 2


def test_reclassify_replaces_old_rows(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", "v1", filename="a.txt")

    rich = StubProvider(default=GOVERNANCE)
    classify_documents(db, cache, rich)
    with connect(db) as conn:
        assert repo.count_rows(conn, "candidate_decisions") == 1

    # New extraction, sparse result -> old candidates gone, not duplicated.
    _write_cache(cache, "doc_a", "v2", filename="a.txt")
    sparse = StubProvider(default={"document_type": "Report", "type_confidence": 0.4})
    classify_documents(db, cache, sparse)
    with connect(db) as conn:
        assert repo.count_classifications(conn) == 1
        assert repo.count_rows(conn, "candidate_decisions") == 0


def test_single_artifact_only(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", "a", filename="a.txt")
    _write_cache(cache, "doc_b", "b", filename="b.txt")

    provider = StubProvider()
    stats = classify_documents(db, cache, provider, artifact_id="doc_a")
    assert stats.documents_processed == 1
    with connect(db) as conn:
        assert repo.count_classifications(conn) == 1
        assert repo.get_classification(conn, "doc_b") is None


def test_classify_ignores_stale_cache_dirs_when_active_artifacts_exist(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_active", "active", filename="active.txt")
    _write_cache(cache, "doc_stale", "stale", filename="stale.txt")

    from catalog.db import init_db

    init_db(db)
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO artifacts(
                path, id, filename, file_type, scan_status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("/tmp/active.txt", "doc_active", "active.txt", ".txt", "RAW"),
        )
        conn.commit()

    provider = StubProvider()
    stats = classify_documents(db, cache, provider)

    assert stats.documents_processed == 1
    assert provider.calls == 1
    with connect(db) as conn:
        assert repo.get_classification(conn, "doc_active") is not None
        assert repo.get_classification(conn, "doc_stale") is None


def test_parse_error_counts_as_error_not_crash(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_bad", "x", filename="bad.txt")

    class BadProvider(BaseLLMProvider):
        def generate(self, prompt, *, system=None):
            return "this is not json"

    stats = classify_documents(db, cache, BadProvider("m"))
    assert stats.errors == 1
    assert stats.documents_processed == 0
    with connect(db) as conn:
        assert repo.count_classifications(conn) == 0


def test_records_classification_run(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", "a", filename="a.txt")
    classify_documents(db, cache, StubProvider())
    with connect(db) as conn:
        run = repo.latest_classification_run(conn)
    assert run["documents_processed"] == 1
    assert run["model"] == "stub-model"
    assert run["completed_at"] is not None


def test_analytics_surface_knowledge(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_1", "one", filename="one.pptx")
    _write_cache(cache, "doc_2", "two", filename="two.pptx")

    # Both documents share a capability and a concept; doc_2 adds a domain so the
    # Launchpad concept connects two domains.
    doc2 = json.loads(json.dumps(GOVERNANCE))
    doc2["domains"] = [{"domain": "Architecture", "confidence": 0.8}]
    provider = StubProvider(responses={"one.pptx": GOVERNANCE, "two.pptx": doc2})
    classify_documents(db, cache, provider)

    with connect(db) as conn:
        types = analytics.document_types(conn)
        assert {t["name"] for t in types} == {"Governance"}

        caps = analytics.top_capabilities(conn)
        assert caps[0]["name"] == "Release Management"
        assert caps[0]["documents"] == 2

        techs = analytics.top_technologies(conn)
        assert techs[0]["name"] == "SAP"

        domains = analytics.top_domains(conn)
        names = {d["name"] for d in domains}
        assert {"Test & Release", "Architecture"} <= names

        themes = analytics.decision_themes(conn)
        assert themes[0]["documents"] == 2  # same decision in both docs

        concepts = analytics.concepts_connecting_domains(conn, min_domains=2)
        assert any(c["name"] == "Launchpad Model" for c in concepts)
