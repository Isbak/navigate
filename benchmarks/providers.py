"""Deterministic stub providers for the benchmark's LLM-driven stages.

These mirror the ``StubProvider`` pattern used across the test-suite
(``tests/test_semantic_classification.py``): a provider is just a
:class:`BaseLLMProvider` whose ``generate`` returns canned text. Because the
real services are provider-agnostic, swapping in a stub makes classify and ask
fully reproducible with no API key or network.
"""

from __future__ import annotations

import json

from catalog.semantic.providers.base import BaseLLMProvider


class StubClassifyProvider(BaseLLMProvider):
    """Return a canned classification JSON per document, keyed by filename.

    The classification prompt embeds the artifact's filename, so matching on it
    routes each document to its gold response. Unknown documents fall back to a
    low-confidence "Other" classification (the same defensive default the real
    parser would land on for an unusable response).
    """

    def __init__(self, responses: dict[str, dict], model: str = "stub-classify") -> None:
        super().__init__(model)
        self.responses = responses
        self.calls = 0

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.calls += 1
        for filename, payload in self.responses.items():
            if filename in prompt:
                return json.dumps(payload)
        return json.dumps({"document_type": "Other", "type_confidence": 0.3})


class StubAnswerProvider(BaseLLMProvider):
    """Return a fixed, grounded-sounding answer for the GraphRAG assistant.

    The assistant only uses the model for *reasoning text*; the benchmark's ask
    metrics are computed from retrieval, support, and citations - never the prose
    - so a constant answer is sufficient and deterministic. It must differ from
    ``NO_EVIDENCE_RESPONSE`` so a retrieved, evidence-backed answer is marked
    supported.
    """

    def __init__(self, model: str = "stub-answer") -> None:
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.calls += 1
        return "Based on the retrieved knowledge graph and its evidence, here is the answer."


__all__ = ["StubClassifyProvider", "StubAnswerProvider"]
