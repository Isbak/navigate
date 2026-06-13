"""GraphRAG Knowledge Assistant (Prompt #9).

A conversational analyst that answers questions over the **approved** knowledge
graph. Unlike naive RAG, it never searches raw document text, never embeds, and
never sends document collections to the model. Retrieval is *graph-first*:

    Question
        -> Intent analysis        (what is asked, of what, how to reason)
        -> Graph retrieval        (match objects, expand neighbourhood via SPARQL)
        -> Evidence retrieval     (approved relationships + supporting quotes)
        -> Context builder        (compact, deterministic, traceable context)
        -> LLM                    (reasoning only, over the supplied context)
        -> Traceable answer       (objects + relationships + evidence + confidence)

The LLM does the *reasoning*; the graph does the *retrieval*. Every answer is
traceable back to a knowledge object, a relationship, evidence, and a document,
and unsupported claims are rejected: if no evidence is retrieved the assistant
says "No supporting evidence found." rather than guessing.

The package layers cleanly on top of the existing query layer
(:mod:`catalog.graph`): it reuses :class:`~catalog.graph.client.GraphClient` for
SPARQL, the NetworkX projection for neighbourhood expansion and paths, and the
:class:`~catalog.semantic.providers.BaseLLMProvider` abstraction so Ollama,
OpenAI, or any future provider works unchanged.
"""

from __future__ import annotations

from .assistant import Answer, Citations, GraphRAGAssistant
from .confidence import ConfidenceComponents, confidence_band, score_confidence
from .context import ContextBundle, build_context
from .intent import Intent, ReasoningType, analyze_intent
from .memory import ConversationMemory, Turn
from .observability import Trace
from .retrieval import (
    GraphRetrieval,
    GraphRetriever,
    RetrievedEvidence,
    RetrievedObject,
    RetrievedRelationship,
)

__all__ = [
    "Answer",
    "Citations",
    "GraphRAGAssistant",
    "ConfidenceComponents",
    "confidence_band",
    "score_confidence",
    "ContextBundle",
    "build_context",
    "Intent",
    "ReasoningType",
    "analyze_intent",
    "ConversationMemory",
    "Turn",
    "Trace",
    "GraphRetrieval",
    "GraphRetriever",
    "RetrievedEvidence",
    "RetrievedObject",
    "RetrievedRelationship",
]
