import json

from catalog.db import connect
from catalog.semantic import repository as repo
from catalog.semantic.config import LLMConfig, RoutingConfig, load_llm_config
from catalog.semantic.providers.base import BaseLLMProvider
from catalog.semantic.routing import (
    ProviderRouter,
    RoutingPolicy,
    build_router,
    profile_document,
    single_provider_router,
)
from catalog.semantic.service import classify_documents


class RecordingProvider(BaseLLMProvider):
    """Returns a canned payload and records that it was called."""

    def __init__(self, model, payload):
        super().__init__(model)
        self.payload = payload
        self.calls = 0

    def generate(self, prompt, *, system=None):
        self.calls += 1
        return json.dumps(self.payload)


# --- complexity profiling -------------------------------------------------


def test_plain_short_document_scores_low():
    profile = profile_document("A short meeting note about the project plan.")
    assert profile.score < 0.5
    assert not profile.forces_deep


def test_standards_document_forces_deep():
    text = (
        "Article 1: The controller shall implement measures. Clause 2 of this "
        "regulation shall apply. Annex A lists the obligations that shall be met."
    )
    profile = profile_document(text)
    assert profile.standard_hits >= 4
    assert profile.forces_deep


def test_equation_document_forces_deep():
    text = r"The design resistance is V_{Rd} = \frac{a}{b} and \sqrt{f_ck} applies."
    profile = profile_document(text)
    assert profile.equation_hits >= 2
    assert profile.forces_deep


def test_long_document_raises_score():
    short = profile_document("word " * 20)
    long = profile_document("word " * 20000)
    assert long.score > short.score


# --- routing decisions ----------------------------------------------------


def _router(policy):
    fast = RecordingProvider("fast-model", {"document_type": "Report"})
    deep = RecordingProvider("deep-model", {"document_type": "Governance"})
    return ProviderRouter(fast=fast, deep=deep, policy=policy), fast, deep


def test_disabled_router_always_returns_single_provider():
    provider = RecordingProvider("only", {})
    router = single_provider_router(provider, max_chunks=20)
    decision = router.route("anything at all")
    assert decision.provider is provider
    assert decision.max_chunks == 20
    assert not router.should_escalate(decision, type_confidence=0.0)


def test_router_sends_simple_doc_to_fast_and_complex_to_deep():
    router, fast, deep = _router(RoutingPolicy(enabled=True, complexity_threshold=0.5))
    simple = router.route("a short ordinary note")
    assert simple.tier == "fast"
    assert simple.provider is fast
    assert simple.max_chunks == router.policy.fast_max_chunks

    complex_doc = router.route(
        "Article 32 of the regulation shall apply. Clause 5 shall be met. "
        "Annex obligations shall hold."
    )
    assert complex_doc.tier == "deep"
    assert complex_doc.provider is deep


def test_router_escalates_low_confidence_fast_results():
    router, _, _ = _router(
        RoutingPolicy(enabled=True, escalate_below_confidence=0.6)
    )
    decision = router.route("a short ordinary note")
    assert decision.tier == "fast"
    assert router.should_escalate(decision, type_confidence=0.4)
    assert not router.should_escalate(decision, type_confidence=0.9)


# --- build_router from config --------------------------------------------


def test_build_router_disabled_returns_single_provider():
    cfg = LLMConfig(provider="claude", model="claude-sonnet-4-5")
    router = build_router(cfg)
    assert router.policy.enabled is False
    assert router.deep_provider.model == "claude-sonnet-4-5"


def test_build_router_enabled_builds_two_models():
    cfg = LLMConfig(
        provider="claude",
        model="claude-sonnet-4-5",
        routing=RoutingConfig(
            enabled=True,
            fast_model="claude-haiku-4-5",
            deep_model="claude-sonnet-4-5",
        ),
    )
    router = build_router(cfg)
    assert router.policy.enabled is True
    assert router.deep_provider.model == "claude-sonnet-4-5"
    decision = router.route("a short ordinary note")
    assert decision.provider.model == "claude-haiku-4-5"


def test_load_llm_config_parses_routing_block(tmp_path):
    path = tmp_path / "llm.yml"
    path.write_text(
        "provider: claude\n"
        "claude:\n  model: claude-sonnet-4-5\n"
        "routing:\n"
        "  enabled: true\n"
        "  fast_model: claude-haiku-4-5\n"
        "  deep_model: claude-sonnet-4-5\n"
        "  complexity_threshold: 0.4\n"
        "  fast_max_chunks: 3\n",
        encoding="utf-8",
    )
    cfg = load_llm_config(path)
    assert cfg.routing.enabled is True
    assert cfg.routing.fast_model == "claude-haiku-4-5"
    assert cfg.routing.complexity_threshold == 0.4
    assert cfg.routing.fast_max_chunks == 3


# --- service integration --------------------------------------------------


def _write_cache(cache_dir, artifact_id, text, filename=None):
    d = cache_dir / artifact_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "extracted.txt").write_text(text, encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps({"artifact_id": artifact_id, "filename": filename or artifact_id}),
        encoding="utf-8",
    )


def test_service_routes_simple_document_to_fast_model(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_note", "a short ordinary meeting note", filename="note.txt")

    fast = RecordingProvider("fast-model", {"document_type": "Meeting Notes", "type_confidence": 0.9})
    deep = RecordingProvider("deep-model", {"document_type": "Governance", "type_confidence": 0.9})
    router = ProviderRouter(fast=fast, deep=deep, policy=RoutingPolicy(enabled=True))

    classify_documents(db, cache, fast, router=router, track_cost=False)

    assert fast.calls == 1
    assert deep.calls == 0
    with connect(db) as conn:
        row = repo.get_classification(conn, "doc_note")
        assert row["model"] == "fast-model"


def test_service_escalates_low_confidence_to_deep_model(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_note", "a short ordinary meeting note", filename="note.txt")

    fast = RecordingProvider("fast-model", {"document_type": "Other", "type_confidence": 0.2})
    deep = RecordingProvider("deep-model", {"document_type": "Report", "type_confidence": 0.95})
    router = ProviderRouter(
        fast=fast, deep=deep, policy=RoutingPolicy(enabled=True, escalate_below_confidence=0.6)
    )

    classify_documents(db, cache, fast, router=router, track_cost=False)

    assert fast.calls == 1
    assert deep.calls == 1  # escalated
    with connect(db) as conn:
        row = repo.get_classification(conn, "doc_note")
        assert row["model"] == "deep-model"
        assert row["document_type"] == "Report"


def test_service_routes_complex_document_to_deep_model(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(
        cache,
        "doc_std",
        "Article 32 shall apply. Clause 5 shall be met. Annex obligations shall hold.",
        filename="iso.pdf",
    )

    fast = RecordingProvider("fast-model", {"document_type": "Report", "type_confidence": 0.9})
    deep = RecordingProvider("deep-model", {"document_type": "Governance", "type_confidence": 0.9})
    router = ProviderRouter(fast=fast, deep=deep, policy=RoutingPolicy(enabled=True))

    classify_documents(db, cache, fast, router=router, track_cost=False)

    assert deep.calls == 1
    assert fast.calls == 0
