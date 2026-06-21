"""Adaptive LLM routing for the classification layer.

The classification service makes one LLM call per document chunk, and that call
is the dominant cost of the whole pipeline. Spending a strong (expensive, slow)
model on every document is wasteful: most documents are ordinary prose a small,
fast model classifies just as well. The few that genuinely need a strong model -
standards and regulations, engineering codes full of equations, long and dense
designs - are a minority, and we can recognise them *deterministically* before
spending a single token.

This module turns that observation into an **adaptive routing policy**:

* :func:`profile_document` scores a document's complexity with cheap, rule-based
  heuristics (length, symbol density, equation markers, normative/standard
  language) - no LLM, so it is fast and free.
* :class:`ProviderRouter` uses that score to pick a *fast* model for simple
  documents and a *deep* model for complex ones, and caps the chunk budget so a
  short, simple document never pays the full per-document chunk allowance.
* When the fast model classifies a document but comes back *unsure*
  (low ``type_confidence``), the service escalates that one document to the deep
  model - a safety net that spends the strong model only where it is needed.

The router is provider-agnostic: it is handed already-built
:class:`~catalog.semantic.providers.base.BaseLLMProvider` instances, so it works
for any backend that offers a small and a large model. When routing is disabled
(the single-model case) it degrades to always returning the one provider, so the
service code path is identical with or without routing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .config import LLMConfig
from .providers import build_provider
from .providers.base import BaseLLMProvider

# Markers that strongly suggest formulas / equations: relational and math
# operators, LaTeX commands, super/subscripts, math delimiters.
_EQUATION_RE = re.compile(r"\\frac|\\sum|\\sqrt|\\int|\$\$|\\\[|\\\(|\^\{|_\{|≤|≥|≈|∑|√|∫|×")

# Phrases that mark a normative / standards / regulation document. These deserve
# the deep model because requirement and equation extraction hinge on them.
_STANDARD_TERMS = (
    "shall ",
    "must not",
    " iso ",
    " iec ",
    "gdpr",
    "nis2",
    "article ",
    "clause ",
    "regulation",
    "directive",
    "eurocode",
    "annex ",
    "obligation",
    "compliance",
)


@dataclass(frozen=True)
class ComplexityProfile:
    """A deterministic, LLM-free read on how hard a document is to classify.

    ``score`` is a 0.0-1.0 blend of the individual signals; ``forces_deep`` is a
    hard override for content (equations, normative language) that the strong
    model handles markedly better regardless of the blended score.
    """

    char_count: int
    symbol_ratio: float
    equation_hits: int
    standard_hits: int
    score: float
    forces_deep: bool


@dataclass(frozen=True)
class RoutingPolicy:
    """Thresholds that turn a :class:`ComplexityProfile` into a routing choice."""

    enabled: bool = False
    # Documents at or above this complexity score go to the deep model.
    complexity_threshold: float = 0.5
    # After a fast-model pass, escalate to the deep model when the document-type
    # confidence is below this.
    escalate_below_confidence: float = 0.6
    # Chunk budget for the fast (simple-document) path; bounds cost on the long
    # tail of simple files. The deep path uses ``default_max_chunks``.
    fast_max_chunks: int = 6
    default_max_chunks: int = 20
    # Length (chars) treated as "fully complex" for the length signal.
    long_document_chars: int = 60_000


@dataclass(frozen=True)
class RouteDecision:
    """The provider and chunk budget chosen for one document."""

    provider: BaseLLMProvider
    tier: str  # "fast" or "deep"
    profile: ComplexityProfile
    max_chunks: int


def profile_document(
    text: str,
    metadata: dict | None = None,
    *,
    long_document_chars: int = RoutingPolicy.long_document_chars,
) -> ComplexityProfile:
    """Score ``text`` for classification complexity without calling an LLM.

    The signals are intentionally simple and explainable: longer documents, a
    high ratio of symbols (maths, tables, code), equation markers, and normative
    "standards" language all push the score up. Equation or standards content
    sets ``forces_deep`` so it is never routed to the fast model on a low score
    alone.
    """

    body = text or ""
    char_count = len(body)
    lowered = body.lower()

    non_space = [c for c in body if not c.isspace()]
    symbols = sum(1 for c in non_space if not c.isalnum())
    symbol_ratio = (symbols / len(non_space)) if non_space else 0.0

    equation_hits = len(_EQUATION_RE.findall(body))
    standard_hits = sum(lowered.count(term) for term in _STANDARD_TERMS)

    # A file_type hint: spreadsheets and PDFs of codes skew complex, but we keep
    # this light - content signals dominate.
    file_type = str((metadata or {}).get("file_type", "")).lower()

    length_signal = min(1.0, char_count / max(1, long_document_chars))
    symbol_signal = min(1.0, symbol_ratio / 0.30)  # 30%+ symbols is very dense
    equation_signal = min(1.0, equation_hits / 5.0)
    standard_signal = min(1.0, standard_hits / 8.0)

    score = (
        0.35 * length_signal
        + 0.20 * symbol_signal
        + 0.25 * equation_signal
        + 0.20 * standard_signal
    )
    if file_type == "xlsx":
        score = min(1.0, score + 0.1)

    forces_deep = equation_hits >= 2 or standard_hits >= 4

    return ComplexityProfile(
        char_count=char_count,
        symbol_ratio=round(symbol_ratio, 4),
        equation_hits=equation_hits,
        standard_hits=standard_hits,
        score=round(min(1.0, score), 4),
        forces_deep=forces_deep,
    )


class ProviderRouter:
    """Selects between a fast and a deep provider per document.

    With ``policy.enabled`` false, ``fast`` and ``deep`` are the same provider
    and every document is routed to it with the default chunk budget - identical
    behaviour to the pre-routing single-model service.
    """

    def __init__(
        self,
        *,
        fast: BaseLLMProvider,
        deep: BaseLLMProvider,
        policy: RoutingPolicy,
    ) -> None:
        self._fast = fast
        self._deep = deep
        self.policy = policy

    @property
    def deep_provider(self) -> BaseLLMProvider:
        return self._deep

    @property
    def primary_model(self) -> str:
        """The model recorded as the run's headline model (the deep one)."""

        return self._deep.model

    def route(self, text: str, metadata: dict | None = None) -> RouteDecision:
        profile = profile_document(
            text, metadata, long_document_chars=self.policy.long_document_chars
        )
        if not self.policy.enabled:
            return RouteDecision(
                self._deep, "deep", profile, self.policy.default_max_chunks
            )
        if profile.forces_deep or profile.score >= self.policy.complexity_threshold:
            return RouteDecision(
                self._deep, "deep", profile, self.policy.default_max_chunks
            )
        return RouteDecision(self._fast, "fast", profile, self.policy.fast_max_chunks)

    def should_escalate(self, decision: RouteDecision, type_confidence: float) -> bool:
        """True when a fast-model result is too uncertain to trust."""

        return (
            self.policy.enabled
            and decision.tier == "fast"
            and self._deep is not self._fast
            and type_confidence < self.policy.escalate_below_confidence
        )


def single_provider_router(
    provider: BaseLLMProvider, *, max_chunks: int
) -> ProviderRouter:
    """A disabled router that always returns ``provider`` - the single-model case."""

    return ProviderRouter(
        fast=provider,
        deep=provider,
        policy=RoutingPolicy(enabled=False, default_max_chunks=max_chunks),
    )


def build_router(config: LLMConfig, *, factory=build_provider) -> ProviderRouter:
    """Build a router from ``config``.

    When ``config.routing`` is disabled (or its fast/deep models are unset) this
    returns a single-provider router over the configured model, so callers can
    always build a router and let it decide whether routing actually happens.
    Otherwise it builds two providers of the active backend - one per model - and
    wires the escalation policy. The fast/deep models must be valid for the
    active provider (e.g. two Claude models when ``provider: claude``).
    """

    rc = config.routing
    base = factory(config)
    if not rc.enabled or not rc.fast_model or not rc.deep_model:
        return single_provider_router(base, max_chunks=config.max_chunks)

    fast = factory(replace(config, model=rc.fast_model))
    deep = (
        base
        if rc.deep_model == config.model
        else factory(replace(config, model=rc.deep_model))
    )
    policy = RoutingPolicy(
        enabled=True,
        complexity_threshold=rc.complexity_threshold,
        escalate_below_confidence=rc.escalate_below_confidence,
        fast_max_chunks=rc.fast_max_chunks,
        default_max_chunks=config.max_chunks,
    )
    return ProviderRouter(fast=fast, deep=deep, policy=policy)


__all__ = [
    "ComplexityProfile",
    "RoutingPolicy",
    "RouteDecision",
    "ProviderRouter",
    "profile_document",
    "single_provider_router",
    "build_router",
]
