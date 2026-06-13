"""The GraphRAG assistant - orchestrating the full question-to-answer pipeline.

This ties the stages together:

    intent -> graph retrieval -> evidence -> context -> LLM -> traceable answer

and exposes the conversational ``ask`` plus the four specialised modes the
prompt asks for: ``explain`` (one object in depth), ``compare`` (two objects),
``impact`` (what a change ripples to), and ``path_reason`` (why two objects are
connected). Every path runs the same graph-first retrieval and the same
hallucination controls, and every answer carries its citations and a computed
confidence band.

The LLM is injected as a :class:`~catalog.semantic.providers.BaseLLMProvider`, so
the assistant is provider-agnostic and trivially testable with a stub.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..graph import network
from ..graph.client import GraphClient
from ..semantic.providers.base import BaseLLMProvider, LLMError
from .confidence import ConfidenceComponents, score_confidence
from .context import ContextBundle, build_context
from .intent import Intent, ReasoningType, analyze_intent
from .memory import ConversationMemory
from .observability import Trace, log_trace
from .prompts import NO_EVIDENCE_RESPONSE, SYSTEM_PROMPT, build_answer_prompt
from .retrieval import DEFAULT_DEPTH, GraphRetrieval, GraphRetriever


@dataclass
class Citations:
    """Everything an answer is traceable back to."""

    objects: list[tuple[str, str]] = field(default_factory=list)  # (id, label)
    evidence: list[tuple[str, str, str]] = field(default_factory=list)  # (handle, doc, quote)
    documents: list[str] = field(default_factory=list)


@dataclass
class Answer:
    """A traceable, graph-backed answer."""

    question: str
    text: str
    intent: Intent
    retrieval: GraphRetrieval
    context: ContextBundle
    confidence: ConfidenceComponents
    citations: Citations
    trace: Trace
    supported: bool
    prompt: str = ""
    referent_note: str | None = None

    @property
    def confidence_band(self) -> str:
        return self.confidence.band

    @property
    def confidence_score(self) -> float:
        return self.confidence.score


class GraphRAGAssistant:
    """Answer questions over the approved knowledge graph, with citations."""

    def __init__(
        self,
        client: GraphClient,
        provider: BaseLLMProvider,
        *,
        memory: ConversationMemory | None = None,
        depth: int = DEFAULT_DEPTH,
    ) -> None:
        self.client = client
        self.provider = provider
        self.graph = network.build_digraph(client)
        self.retriever = GraphRetriever(client, graph=self.graph)
        self.memory = memory if memory is not None else ConversationMemory()
        self.default_depth = depth

    # -- public API -----------------------------------------------------------

    def ask(self, question: str, *, depth: int | None = None) -> Answer:
        """Answer a (possibly follow-up) question, graph-first."""

        depth = self.default_depth if depth is None else depth
        intent = analyze_intent(question, self.retriever.labels().values())

        resolution = self.memory.resolve(
            question, has_focus=bool(intent.focus_terms)
        )
        referent_note = None
        if resolution.is_follow_up:
            referent_note = (
                "The question refers back to: "
                + ", ".join(resolution.carried_labels)
            )

        resolved, unresolved = self.retriever.resolve(intent.focus_terms)
        retrieval = self.retriever.retrieve(
            resolved,
            depth=depth,
            extra_seeds=resolution.carried_ids,
            unresolved=unresolved,
        )

        answer = self._answer_from_retrieval(
            question, intent, retrieval, referent_note=referent_note
        )
        self._remember(question, retrieval)
        return answer

    def explain(self, term: str, *, depth: int | None = None) -> Answer:
        """Explain one object: description, connections, and supporting evidence."""

        depth = self.default_depth if depth is None else depth
        question = f"Explain {term}: what it is, what it connects to, and the evidence."
        intent = Intent(
            question=question,
            reasoning_type=ReasoningType.DOMAIN,
            focus_terms=[term],
        )
        resolved, unresolved = self.retriever.resolve([term])
        retrieval = self.retriever.retrieve(resolved, depth=depth, unresolved=unresolved)
        answer = self._answer_from_retrieval(question, intent, retrieval)
        self._remember(question, retrieval)
        return answer

    def impact(self, term: str, *, depth: int | None = None) -> Answer:
        """Summarise what a change to an object would affect."""

        depth = self.default_depth if depth is None else depth
        question = f"What is the impact of {term}? What capabilities, decisions, risks, and teams are affected?"
        intent = Intent(
            question=question,
            reasoning_type=ReasoningType.IMPACT,
            focus_terms=[term],
            relationship_focus="affects",
        )
        resolved, unresolved = self.retriever.resolve([term])
        retrieval = self.retriever.retrieve(resolved, depth=depth, unresolved=unresolved)
        answer = self._answer_from_retrieval(question, intent, retrieval)
        self._remember(question, retrieval)
        return answer

    def compare(self, term_a: str, term_b: str, *, depth: int | None = None) -> Answer:
        """Compare two objects: shared and unique concepts, evidence, differences."""

        depth = self.default_depth if depth is None else depth
        question = f"Compare {term_a} and {term_b}: shared concepts, unique concepts, and differences."
        intent = Intent(
            question=question,
            reasoning_type=ReasoningType.COMPARISON,
            focus_terms=[term_a, term_b],
        )
        resolved, unresolved = self.retriever.resolve([term_a, term_b])
        retrieval = self.retriever.retrieve(resolved, depth=depth, unresolved=unresolved)
        answer = self._answer_from_retrieval(question, intent, retrieval)
        self._remember(question, retrieval)
        return answer

    def path_reason(
        self, term_a: str, term_b: str, *, depth: int | None = None
    ) -> Answer:
        """Retrieve the graph path between two objects and have the LLM explain it.

        Unlike the other modes, retrieval is anchored on the *path* between the
        endpoints (not a radial neighbourhood), so the context is exactly the
        chain of objects and relationships the reasoning will walk.
        """

        question = f"What connects {term_a} to {term_b}?"
        intent = Intent(
            question=question,
            reasoning_type=ReasoningType.PATH,
            focus_terms=[term_a, term_b],
        )
        (src, _), (tgt, _) = self._resolve_pair(term_a, term_b)
        unresolved = [t for t, node in ((term_a, src), (term_b, tgt)) if node is None]

        if src is None or tgt is None:
            retrieval = self.retriever.retrieve(
                [n for n in (src, tgt) if n], depth=self.default_depth if depth is None else depth,
                unresolved=unresolved,
            )
            return self._answer_from_retrieval(question, intent, retrieval)

        hops = network.shortest_path(self.graph, src, tgt)
        if hops is None:
            # No path: retrieve both neighbourhoods so the answer can say why.
            retrieval = self.retriever.retrieve([src, tgt], depth=1, unresolved=unresolved)
            return self._answer_from_retrieval(question, intent, retrieval)

        path_ids = [src] + [hop["to"] for hop in hops]
        retrieval = self.retriever.retrieve(path_ids, depth=0, unresolved=unresolved)
        answer = self._answer_from_retrieval(question, intent, retrieval)
        self._remember(question, retrieval)
        return answer

    # -- shared pipeline tail -------------------------------------------------

    def _answer_from_retrieval(
        self,
        question: str,
        intent: Intent,
        retrieval: GraphRetrieval,
        *,
        referent_note: str | None = None,
    ) -> Answer:
        start = time.perf_counter()
        context = build_context(retrieval, intent)
        confidence = score_confidence(retrieval)

        # Hallucination control: with no matched object or no evidence there is
        # nothing trustworthy to answer from, so we decline *before* the model.
        if not retrieval.has_support:
            text = NO_EVIDENCE_RESPONSE
            prompt = ""
            supported = False
        else:
            prompt = build_answer_prompt(
                question,
                context.text,
                reasoning_type=str(intent.reasoning_type),
                referent_note=referent_note,
            )
            text = self._generate(prompt)
            supported = text.strip() != NO_EVIDENCE_RESPONSE

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        citations = self._citations(retrieval, context) if supported else Citations()
        trace = Trace(
            question=question,
            reasoning_type=str(intent.reasoning_type),
            objects_retrieved=len(retrieval.objects),
            relationships_retrieved=len(retrieval.relationships),
            evidence_count=len(retrieval.evidence),
            prompt_size=len(prompt),
            response_time_ms=elapsed_ms,
            confidence_band=confidence.band,
        )
        log_trace(trace)

        return Answer(
            question=question,
            text=text,
            intent=intent,
            retrieval=retrieval,
            context=context,
            confidence=confidence,
            citations=citations,
            trace=trace,
            supported=supported,
            prompt=prompt,
            referent_note=referent_note,
        )

    def _generate(self, prompt: str) -> str:
        try:
            return self.provider.generate(prompt, system=SYSTEM_PROMPT).strip()
        except LLMError as exc:
            # Surface the failure honestly rather than fabricating an answer.
            return f"[LLM error: {exc}]"

    def _citations(self, retrieval: GraphRetrieval, context: ContextBundle) -> Citations:
        objects = [(o.id, o.label) for o in retrieval.objects if o.is_seed]
        if not objects:
            objects = [(o.id, o.label) for o in retrieval.objects[:5]]
        evidence = [
            (handle, item.artifact_id, item.quote)
            for handle, item in context.evidence_handles.items()
        ]
        return Citations(
            objects=objects,
            evidence=evidence,
            documents=retrieval.documents,
        )

    def _resolve_pair(self, term_a: str, term_b: str):
        resolved_a, _ = self.retriever.resolve([term_a])
        resolved_b, _ = self.retriever.resolve([term_b])
        node_a = resolved_a[0] if resolved_a else None
        node_b = resolved_b[0] if resolved_b else None
        return (node_a, term_a), (node_b, term_b)

    def _remember(self, question: str, retrieval: GraphRetrieval) -> None:
        seed_objs = [o for o in retrieval.objects if o.is_seed] or retrieval.objects[:3]
        self.memory.record(
            question,
            [o.id for o in seed_objs],
            [o.label for o in seed_objs],
        )


__all__ = ["Answer", "Citations", "GraphRAGAssistant"]
