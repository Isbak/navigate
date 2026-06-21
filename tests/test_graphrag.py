"""Unit tests for the GraphRAG knowledge assistant (Prompt #9).

Covers the eight areas the prompt calls out: intent detection, SPARQL/graph
retrieval, context building, evidence retrieval, citation generation,
hallucination controls, follow-up questions, and confidence scoring. The
``approved_graph`` fixture (see ``conftest.py``) seeds the documented Release
Governance example, so these tests run fully offline with no LLM or Fuseki.
"""

from __future__ import annotations

import pytest

from catalog.db import connect
from catalog.graph.client import GraphClient
from catalog.graphrag.assistant import GraphRAGAssistant
from catalog.graphrag.confidence import confidence_band, score_confidence
from catalog.graphrag.context import build_context
from catalog.graphrag.intent import ReasoningType, analyze_intent
from catalog.graphrag.prompts import NO_EVIDENCE_RESPONSE
from catalog.graphrag.retrieval import GraphRetriever
from catalog.semantic.providers.base import BaseLLMProvider, LLMError

KNOWN_LABELS = [
    "Release Governance",
    "Release Management",
    "Launchpad Model",
    "Test & Release Team",
    "Salesforce",
]


class StubProvider(BaseLLMProvider):
    """Deterministic provider that echoes a cited answer."""

    def __init__(self, model: str = "stub", text: str | None = None) -> None:
        super().__init__(model)
        self._text = text

    def generate(self, prompt, *, system=None):
        self.last_prompt = prompt
        self.last_system = system
        if self._text is not None:
            return self._text
        return "Release Governance supports the Launchpad Model [E1]."


class FailingProvider(BaseLLMProvider):
    def generate(self, prompt, *, system=None):
        raise LLMError("boom")


@pytest.fixture
def client(approved_graph):
    with connect(approved_graph.db) as conn:
        yield GraphClient.from_sqlite(conn)


@pytest.fixture
def retriever(client):
    return GraphRetriever(client)


@pytest.fixture
def assistant(client):
    return GraphRAGAssistant(client, StubProvider())


# -- intent detection ---------------------------------------------------------

def test_intent_lookup():
    intent = analyze_intent("What documents support Release Governance?", KNOWN_LABELS)
    assert intent.reasoning_type is ReasoningType.LOOKUP
    assert "Release Governance" in intent.focus_terms
    assert intent.relationship_focus == "supports"


def test_intent_depends_on_and_type():
    intent = analyze_intent("What capabilities depend on Salesforce?", KNOWN_LABELS)
    assert intent.relationship_focus == "depends_on"
    assert intent.object_type_focus == "Capability"
    assert intent.focus_terms == ["Salesforce"]


def test_intent_path():
    intent = analyze_intent(
        "What connects Launchpad Model to Release Management?", KNOWN_LABELS
    )
    assert intent.reasoning_type is ReasoningType.PATH
    assert intent.focus_terms == ["Launchpad Model", "Release Management"]
    assert intent.needs_two_objects


def test_intent_impact():
    intent = analyze_intent("What is the impact of Salesforce?", KNOWN_LABELS)
    assert intent.reasoning_type is ReasoningType.IMPACT


def test_intent_comparison():
    intent = analyze_intent(
        "Compare Release Governance and Release Management", KNOWN_LABELS
    )
    assert intent.reasoning_type is ReasoningType.COMPARISON
    assert intent.focus_terms == ["Release Governance", "Release Management"]


def test_intent_evidence_and_referent():
    intent = analyze_intent("What evidence supports this conclusion?", KNOWN_LABELS)
    assert intent.reasoning_type is ReasoningType.EVIDENCE
    assert intent.evidence_focus
    assert intent.has_referent


def test_intent_focus_terms_drop_substrings():
    # "Release" must not be reported when "Release Governance" matched.
    intent = analyze_intent("Tell me about Release Governance", KNOWN_LABELS + ["Release"])
    assert "Release Governance" in intent.focus_terms
    assert "Release" not in intent.focus_terms


# -- graph / SPARQL retrieval -------------------------------------------------

def test_retriever_resolves_terms(retriever):
    resolved, unresolved = retriever.resolve(["Release Governance", "Nope"])
    assert "capability_release_governance" in resolved
    assert unresolved == ["Nope"]


def test_retriever_expands_neighbourhood(retriever):
    seeds = ["capability_release_governance"]
    depth1 = retriever.expand(seeds, 1)
    depth2 = retriever.expand(seeds, 2)
    assert depth1["capability_release_governance"] == 0
    # Direct neighbours appear at depth 1.
    assert "decision_launchpad_model" in depth1
    # Salesforce is two hops away (via Release Management) - only at depth 2.
    assert "platform_salesforce" not in depth1
    assert "platform_salesforce" in depth2


def test_retriever_records_sparql(retriever):
    retrieval = retriever.retrieve(["capability_release_governance"], depth=1)
    assert retrieval.sparql, "executed SPARQL should be recorded"
    assert any("supportedBy" in q for q in retrieval.sparql)


def test_retrieve_collects_relationships(retriever):
    retrieval = retriever.retrieve(["capability_release_governance"], depth=2)
    pairs = {(r.source, r.predicate, r.target) for r in retrieval.relationships}
    assert (
        "capability_release_governance",
        "supports",
        "decision_launchpad_model",
    ) in pairs


# -- evidence retrieval -------------------------------------------------------

def test_evidence_retrieval(retriever):
    retrieval = retriever.retrieve(["capability_release_governance"], depth=0)
    assert retrieval.evidence
    rg = [e for e in retrieval.evidence if e.object_id == "capability_release_governance"]
    assert rg and rg[0].artifact_id == "doc_a"
    assert rg[0].quote


def test_documents_are_distinct_and_ordered(retriever):
    retrieval = retriever.retrieve(["capability_release_governance"], depth=2)
    docs = retrieval.documents
    assert len(docs) == len(set(docs))
    assert "doc_a" in docs


# -- context building ---------------------------------------------------------

def test_context_is_structured_and_traceable(retriever):
    retrieval = retriever.retrieve(["capability_release_governance"], depth=1)
    intent = analyze_intent("What supports Release Governance?", KNOWN_LABELS)
    bundle = build_context(retrieval, intent)
    assert "KNOWLEDGE OBJECTS:" in bundle.text
    assert "RELATIONSHIPS:" in bundle.text
    assert "EVIDENCE:" in bundle.text
    assert "[E1]" in bundle.text
    assert bundle.evidence_handles  # handle map populated for citations
    assert "Release Governance" in bundle.text


def test_context_deterministic(retriever):
    retrieval = retriever.retrieve(["capability_release_governance"], depth=2)
    a = build_context(retrieval)
    b = build_context(retrieval)
    assert a.text == b.text


# -- confidence scoring -------------------------------------------------------

def test_confidence_band_thresholds():
    assert confidence_band(0.9) == "High"
    assert confidence_band(0.6) == "Medium"
    assert confidence_band(0.2) == "Low"


def test_confidence_scoring_with_evidence(retriever):
    retrieval = retriever.retrieve(["capability_release_governance"], depth=2)
    components = score_confidence(retrieval)
    assert 0.0 <= components.score <= 1.0
    assert components.evidence_confidence > 0
    assert components.coverage == 1.0
    assert components.band in {"High", "Medium", "Low"}


def test_confidence_low_without_evidence(retriever):
    # An unresolved term yields no objects and no evidence -> Low.
    retrieval = retriever.retrieve([], unresolved=["Ghost"])
    components = score_confidence(retrieval)
    assert components.band == "Low"


# -- assistant: answers, citations, hallucination, follow-up ------------------

def test_ask_produces_cited_answer(assistant):
    answer = assistant.ask("What supports Release Governance?")
    assert answer.supported
    assert "[E1]" in answer.text
    assert answer.citations.objects
    assert ("capability_release_governance", "Release Governance") in answer.citations.objects
    assert answer.citations.evidence
    assert answer.citations.documents


def test_ask_prompt_carries_system_and_context(assistant):
    assistant.ask("What supports Release Governance?")
    provider = assistant.provider
    assert "knowledge-graph analyst" in provider.last_system
    assert "GRAPH CONTEXT" in provider.last_prompt
    assert "EVIDENCE" in provider.last_prompt


def test_hallucination_control_declines_without_match(assistant):
    answer = assistant.ask("What supports the Imaginary Platform?")
    assert not answer.supported
    assert answer.text == NO_EVIDENCE_RESPONSE
    assert answer.confidence.band == "Low"
    assert answer.citations.objects == []


def test_followup_resolves_referent(assistant):
    first = assistant.ask("What supports Release Governance?")
    assert first.supported
    second = assistant.ask("What risks are associated with that?")
    # "that" carried Release Governance forward as a seed.
    assert "capability_release_governance" in second.retrieval.seeds
    assert second.referent_note and "Release Governance" in second.referent_note


def test_memory_records_turns(assistant):
    assistant.ask("What supports Release Governance?")
    assert assistant.memory.last is not None
    assert "capability_release_governance" in assistant.memory.last.object_ids


def test_explain_mode(assistant):
    answer = assistant.explain("Release Governance")
    assert answer.supported
    assert answer.intent.reasoning_type is ReasoningType.DOMAIN
    assert any(o.id == "capability_release_governance" for o in answer.retrieval.objects)


def test_impact_mode(assistant):
    answer = assistant.impact("Salesforce")
    assert answer.intent.reasoning_type is ReasoningType.IMPACT
    # Salesforce affects Release Management, reachable in the neighbourhood.
    ids = {o.id for o in answer.retrieval.objects}
    assert "capability_release_management" in ids


def test_compare_mode(assistant):
    answer = assistant.compare("Release Governance", "Release Management")
    assert answer.intent.reasoning_type is ReasoningType.COMPARISON
    ids = {o.id for o in answer.retrieval.objects}
    assert "capability_release_governance" in ids
    assert "capability_release_management" in ids


def test_path_reason_uses_path(assistant):
    answer = assistant.path_reason("Release Governance", "Salesforce")
    assert answer.intent.reasoning_type is ReasoningType.PATH
    ids = [o.id for o in answer.retrieval.objects]
    assert ids[0] == "capability_release_governance"
    assert "platform_salesforce" in ids


def test_trace_observability(assistant):
    answer = assistant.ask("What supports Release Governance?")
    trace = answer.trace
    assert trace.objects_retrieved > 0
    assert trace.evidence_count > 0
    assert trace.prompt_size > 0
    assert trace.reasoning_type == "lookup"


def test_llm_error_is_surfaced(client):
    assistant = GraphRAGAssistant(client, FailingProvider("bad"))
    answer = assistant.ask("What supports Release Governance?")
    assert "LLM error" in answer.text


def test_depth_limits_neighbourhood(client):
    assistant = GraphRAGAssistant(client, StubProvider())
    shallow = assistant.ask("What supports Release Governance?", depth=1)
    deep = assistant.ask("What supports Release Governance?", depth=3)
    shallow_ids = {o.id for o in shallow.retrieval.objects}
    deep_ids = {o.id for o in deep.retrieval.objects}
    assert "platform_salesforce" not in shallow_ids
    assert "platform_salesforce" in deep_ids
