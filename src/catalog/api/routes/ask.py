"""GraphRAG question-answering endpoint.

The assistant calls an external LLM provider, so it is gated behind an explicit
``enable_graphrag`` setting (off by default, honoring the API's "no external
calls unless explicitly triggered" principle). When disabled - or when no LLM
provider can be built - the endpoint returns HTTP 501 with a clear message.
"""

from __future__ import annotations

import dataclasses
import sqlite3

from fastapi import APIRouter, Depends

from ...graph.client import GraphClient
from ...graphrag.assistant import GraphRAGAssistant
from ...semantic.config import load_llm_config
from ...semantic.providers import LLMError, build_provider
from ..config import ApiSettings
from ..dependencies import get_db, get_settings
from ..errors import not_implemented
from ..schemas import AskRequest, AskResponse, CompareRequest, ExplainRequest

router = APIRouter(tags=["ask"])

_NOT_IMPLEMENTED = "GraphRAG assistant is not implemented in this build."


def _assistant(
    conn: sqlite3.Connection, settings: ApiSettings, depth: int
) -> GraphRAGAssistant:
    """Build a GraphRAG assistant, honoring the ``enable_graphrag`` gate.

    Mirrors the "no external calls unless explicitly enabled" principle: raises
    HTTP 501 when GraphRAG is disabled or no LLM provider can be constructed.
    """

    if not settings.enable_graphrag:
        raise not_implemented(_NOT_IMPLEMENTED)
    config = load_llm_config(settings.llm_config)
    try:
        provider = build_provider(config)
    except LLMError as exc:
        raise not_implemented(_NOT_IMPLEMENTED, reason=str(exc)) from exc
    client = GraphClient.from_sqlite(conn, queries_dir=settings.queries_dir)
    return GraphRAGAssistant(client, provider, depth=max(1, depth))


def _answer_response(answer, *, show_evidence: bool, show_context: bool) -> AskResponse:
    return AskResponse(
        answer=answer.text,
        confidence=str(answer.confidence_band),
        objects_used=[{"id": oid, "label": label} for oid, label in answer.citations.objects],
        relationships_used=_relationships(answer),
        evidence_used=(
            [
                {"handle": handle, "document": doc, "quote": quote}
                for handle, doc, quote in answer.citations.evidence
            ]
            if show_evidence
            else []
        ),
        context=answer.context.text if show_context else None,
    )


@router.post("/ask", response_model=AskResponse)
def ask(
    payload: AskRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> AskResponse:
    assistant = _assistant(conn, settings, payload.depth)
    answer = assistant.ask(payload.question, depth=payload.depth)
    return _answer_response(
        answer, show_evidence=payload.show_evidence, show_context=payload.show_context
    )


@router.post("/ask/explain", response_model=AskResponse)
def explain(
    payload: ExplainRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> AskResponse:
    """Explain a single object in depth (what it is, why it matters)."""

    assistant = _assistant(conn, settings, payload.depth)
    answer = assistant.explain(payload.term, depth=payload.depth)
    return _answer_response(
        answer, show_evidence=payload.show_evidence, show_context=payload.show_context
    )


@router.post("/ask/impact", response_model=AskResponse)
def impact(
    payload: ExplainRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> AskResponse:
    """Summarise what a change to a single object would affect."""

    assistant = _assistant(conn, settings, payload.depth)
    answer = assistant.impact(payload.term, depth=payload.depth)
    return _answer_response(
        answer, show_evidence=payload.show_evidence, show_context=payload.show_context
    )


@router.post("/ask/compare", response_model=AskResponse)
def compare(
    payload: CompareRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> AskResponse:
    """Compare two objects side by side (shared and unique concepts)."""

    assistant = _assistant(conn, settings, payload.depth)
    answer = assistant.compare(payload.term_a, payload.term_b, depth=payload.depth)
    return _answer_response(
        answer, show_evidence=payload.show_evidence, show_context=payload.show_context
    )


@router.post("/ask/path-reason", response_model=AskResponse)
def path_reason(
    payload: CompareRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> AskResponse:
    """Explain how and why two objects are connected."""

    assistant = _assistant(conn, settings, payload.depth)
    answer = assistant.path_reason(payload.term_a, payload.term_b, depth=payload.depth)
    return _answer_response(
        answer, show_evidence=payload.show_evidence, show_context=payload.show_context
    )


def _relationships(answer) -> list[dict]:
    out: list[dict] = []
    for rel in getattr(answer.retrieval, "relationships", []) or []:
        if dataclasses.is_dataclass(rel):
            out.append(dataclasses.asdict(rel))
        else:
            out.append(
                {
                    "source": getattr(rel, "source", None) or getattr(rel, "subject", None),
                    "predicate": getattr(rel, "predicate", None),
                    "target": getattr(rel, "target", None) or getattr(rel, "object", None),
                }
            )
    return out
