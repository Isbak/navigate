"""Turn assistant/graph results into plain JSON-able dicts for MCP tools.

The graph-exploration helpers already return plain dicts/lists, so the only real
work is serializing a GraphRAG :class:`~catalog.graphrag.assistant.Answer`. The
shape mirrors the REST API's ``AskResponse`` (``catalog.api.routes.ask``) so a
client sees the same contract on either transport.
"""

from __future__ import annotations

import dataclasses
from typing import Any


def relationships_used(answer: Any) -> list[dict]:
    """Serialize the retrieved relationships behind an answer.

    Mirrors ``catalog.api.routes.ask._relationships``: dataclass relationships are
    dumped wholesale; anything else is reduced to source/predicate/target.
    """

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


def answer_to_dict(
    answer: Any, *, show_evidence: bool = True, show_context: bool = False
) -> dict:
    """Serialize a GraphRAG ``Answer`` into the MCP tool result shape."""

    return {
        "available": True,
        "answer": answer.text,
        "confidence": str(answer.confidence_band),
        "supported": bool(answer.supported),
        "objects_used": [
            {"id": oid, "label": label} for oid, label in answer.citations.objects
        ],
        "relationships_used": relationships_used(answer),
        "evidence_used": (
            [
                {"handle": handle, "document": doc, "quote": quote}
                for handle, doc, quote in answer.citations.evidence
            ]
            if show_evidence
            else []
        ),
        "documents_used": list(answer.citations.documents),
        "context": answer.context.text if show_context else None,
    }


def unavailable(reason: str) -> dict:
    """The structured decline returned when the LLM-backed ``ask`` tool is off.

    Keeps the same top-level keys as a real answer so a client can branch on
    ``available`` without a schema surprise.
    """

    return {
        "available": False,
        "answer": reason,
        "confidence": "Low",
        "supported": False,
        "objects_used": [],
        "relationships_used": [],
        "evidence_used": [],
        "documents_used": [],
        "context": None,
    }


__all__ = ["answer_to_dict", "relationships_used", "unavailable"]
