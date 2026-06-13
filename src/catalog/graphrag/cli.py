"""CLI surface for the GraphRAG assistant (``catalog ask`` and friends).

Wires the assistant into the ``catalog`` command line:

* ``catalog ask "<question>"``   - conversational, graph-backed Q&A
* ``catalog explain <object>``    - one object in depth
* ``catalog compare A B``         - two objects side by side
* ``catalog impact <object>``     - what a change ripples to
* ``catalog path-reason A B``     - why two objects are connected

Advanced flags mirror the prompt: ``--depth`` (1/2/3), ``--model`` (override the
configured LLM), and the ``--show-context`` / ``--show-sparql`` / ``--show-evidence``
inspection switches. By default everything runs against the in-memory projection
built from SQLite, so no Fuseki is required; ``--fuseki`` reroutes SPARQL to a
live endpoint.
"""

from __future__ import annotations

import argparse
import dataclasses

from ..db import connect, init_db
from ..graph.client import GraphClient
from ..rdf.config import load_jena_config
from ..semantic.config import load_llm_config
from ..semantic.providers import LLMError, build_provider
from .assistant import Answer, GraphRAGAssistant
from .retrieval import DEFAULT_DEPTH, MAX_DEPTH


def _build_client(args) -> GraphClient:
    if getattr(args, "fuseki", False):
        config = load_jena_config(args.jena_config)
        return GraphClient.from_fuseki(config, queries_dir=args.queries_dir)
    init_db(args.db)
    with connect(args.db) as conn:
        return GraphClient.from_sqlite(conn, queries_dir=args.queries_dir)


def _build_assistant(args) -> GraphRAGAssistant | None:
    config = load_llm_config(args.llm_config)
    if getattr(args, "model", None):
        config = dataclasses.replace(config, model=args.model)
    try:
        provider = build_provider(config)
    except LLMError as exc:
        print(f"Error: {exc}")
        return None
    client = _build_client(args)
    depth = _clamp_depth(getattr(args, "depth", DEFAULT_DEPTH))
    return GraphRAGAssistant(client, provider, depth=depth)


def _clamp_depth(depth: int | None) -> int:
    if depth is None:
        return DEFAULT_DEPTH
    return max(1, min(int(depth), MAX_DEPTH))


# -- rendering ----------------------------------------------------------------

def _render_answer(args, answer: Answer) -> None:
    print(f"Q: {answer.question}\n")
    if answer.referent_note:
        print(f"({answer.referent_note})\n")
    print(answer.text)

    if answer.supported:
        _render_citations(answer)
    conf = answer.confidence
    print(
        f"\nConfidence: {conf.band} ({conf.score:.2f})  "
        f"[objects {conf.object_confidence:.2f}, "
        f"relationships {conf.relationship_confidence:.2f}, "
        f"evidence {conf.evidence_confidence:.2f}, "
        f"coverage {conf.coverage:.2f}]"
    )

    if getattr(args, "show_evidence", False):
        _render_evidence(answer)
    if getattr(args, "show_context", False):
        print("\n--- CONTEXT ---")
        print(answer.context.text)
    if getattr(args, "show_sparql", False):
        print("\n--- SPARQL ---")
        for query in answer.retrieval.sparql:
            print(query.strip())
            print()


def _render_citations(answer: Answer) -> None:
    citations = answer.citations
    print("\nKnowledge objects used:")
    if citations.objects:
        for object_id, label in citations.objects:
            print(f"  - {label} ({object_id})")
    else:
        print("  (none)")

    print("\nEvidence used:")
    if citations.evidence:
        for handle, document, quote in citations.evidence:
            snippet = (quote[:100] + "...") if len(quote) > 100 else quote
            print(f"  [{handle}] {document}: \"{snippet}\"")
    else:
        print("  (none)")

    print("\nDocuments used:")
    if citations.documents:
        for document in citations.documents:
            print(f"  - {document}")
    else:
        print("  (none)")


def _render_evidence(answer: Answer) -> None:
    print("\n--- EVIDENCE ---")
    if not answer.retrieval.evidence:
        print("  (none retrieved)")
        return
    for index, item in enumerate(answer.retrieval.evidence, start=1):
        print(
            f"  [E{index}] {item.artifact_id} supports {item.object_label} "
            f"(conf {item.confidence:.2f}): \"{item.quote}\""
        )


# -- command handlers ---------------------------------------------------------

def _cmd_ask(args) -> None:
    assistant = _build_assistant(args)
    if assistant is None:
        return
    answer = assistant.ask(args.question, depth=_clamp_depth(args.depth))
    _render_answer(args, answer)


def _cmd_explain(args) -> None:
    assistant = _build_assistant(args)
    if assistant is None:
        return
    answer = assistant.explain(args.object, depth=_clamp_depth(args.depth))
    _render_answer(args, answer)


def _cmd_impact(args) -> None:
    assistant = _build_assistant(args)
    if assistant is None:
        return
    answer = assistant.impact(args.object, depth=_clamp_depth(args.depth))
    _render_answer(args, answer)


def _cmd_compare(args) -> None:
    assistant = _build_assistant(args)
    if assistant is None:
        return
    answer = assistant.compare(args.object1, args.object2, depth=_clamp_depth(args.depth))
    _render_answer(args, answer)


def _cmd_path_reason(args) -> None:
    assistant = _build_assistant(args)
    if assistant is None:
        return
    answer = assistant.path_reason(args.object1, args.object2, depth=_clamp_depth(args.depth))
    _render_answer(args, answer)


# -- parser wiring ------------------------------------------------------------

def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fuseki",
        action="store_true",
        help="run SPARQL against the live Fuseki endpoint instead of SQLite",
    )
    parser.add_argument("--queries-dir", default="queries")
    parser.add_argument(
        "--depth",
        type=int,
        default=DEFAULT_DEPTH,
        help=f"graph expansion depth 1-{MAX_DEPTH} (default {DEFAULT_DEPTH})",
    )
    parser.add_argument("--model", default=None, help="override the configured LLM model")
    parser.add_argument("--show-context", action="store_true", help="print the LLM context")
    parser.add_argument("--show-sparql", action="store_true", help="print the SPARQL run")
    parser.add_argument(
        "--show-evidence", action="store_true", help="print the retrieved evidence"
    )


def add_graphrag_parsers(sub: argparse._SubParsersAction) -> None:
    """Register the GraphRAG commands on the top-level subparsers."""

    ask = sub.add_parser("ask", help="ask the GraphRAG knowledge assistant")
    ask.add_argument("question")
    _add_common_flags(ask)

    explain = sub.add_parser("explain", help="explain one knowledge object")
    explain.add_argument("object")
    _add_common_flags(explain)

    impact = sub.add_parser("impact", help="summarise what an object affects")
    impact.add_argument("object")
    _add_common_flags(impact)

    compare = sub.add_parser("compare", help="compare two knowledge objects")
    compare.add_argument("object1")
    compare.add_argument("object2")
    _add_common_flags(compare)

    path_reason = sub.add_parser(
        "path-reason", help="explain how two objects are connected"
    )
    path_reason.add_argument("object1")
    path_reason.add_argument("object2")
    _add_common_flags(path_reason)


_HANDLERS = {
    "ask": _cmd_ask,
    "explain": _cmd_explain,
    "impact": _cmd_impact,
    "compare": _cmd_compare,
    "path-reason": _cmd_path_reason,
}


def run_graphrag(args) -> None:
    """Dispatch a parsed GraphRAG command."""

    _HANDLERS[args.command](args)


__all__ = ["add_graphrag_parsers", "run_graphrag"]
