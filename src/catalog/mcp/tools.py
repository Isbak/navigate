"""The MCP tool implementations as plain, testable functions.

Each function takes an :class:`~catalog.mcp.config.McpSettings` and returns a
JSON-able dict. They deliberately reuse the existing helpers rather than issuing
their own SQL/SPARQL:

* graph-first tools delegate to ``catalog.graph.cli`` and ``catalog.graph.network``
  (deterministic, offline, no API key);
* :func:`ask` delegates to the GraphRAG assistant and prices its token usage
  through ``catalog.cost.record_calls``, exactly like the CLI and REST API.

Keeping these as free functions (no ``mcp`` import) means they are unit-testable
without the optional MCP runtime installed.
"""

from __future__ import annotations

from ..cost import record_calls
from ..db import connect, init_db
from ..governance import agent_review
from ..governance import service as gov_service
from ..governance.config import load_governance_config
from ..governance.provenance import agent_reviewer
from ..graph import network
from ..graph.cli import _resolve_id, evidence_count, evidence_for, object_detail, search_objects
from ..graph.client import GraphClient
from ..graphrag.assistant import GraphRAGAssistant
from ..knowledge import repository as know_repo
from ..knowledge import service as know_service
from ..knowledge.models import ReviewState
from ..semantic.config import load_llm_config
from ..semantic.providers import LLMError, build_provider
from .config import McpSettings
from .serializers import answer_to_dict, unavailable


def _client(settings: McpSettings) -> GraphClient:
    """Build a SQLite-backed SPARQL client (in-memory projection).

    ``from_sqlite`` reads the approved triples into an rdflib graph at
    construction, so the connection can close immediately — the same pattern as
    ``catalog.graph.cli._make_client``.
    """

    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        return GraphClient.from_sqlite(conn, queries_dir=settings.queries_dir)


def _resolve(client: GraphClient, term: str):
    """Resolve a label or id to a node id against the approved graph.

    Returns ``(object_id, graph, candidates)``; ``object_id`` is ``None`` when the
    term is unknown or ambiguous, with ``candidates`` listing near matches.
    """

    graph = network.build_digraph(client)
    object_id, candidates = _resolve_id(graph, term)
    return object_id, graph, candidates


def _unresolved(term: str, candidates: list[str]) -> dict:
    return {
        "found": False,
        "query": term,
        "candidates": candidates[:10],
        "message": (
            f"{term!r} is ambiguous; pass an id or a more specific name."
            if candidates
            else f"No knowledge object matches {term!r}."
        ),
    }


# -- graph-first tools (offline, no LLM) --------------------------------------

def search_knowledge(settings: McpSettings, term: str) -> dict:
    """Search approved objects by label/description; reuse ``graph.cli.search_objects``."""

    results = search_objects(_client(settings), term)
    return {"query": term, "count": len(results), "results": results}


def get_object(settings: McpSettings, object_id: str) -> dict:
    """Detail for one object (type, description, confidence, evidence count)."""

    client = _client(settings)
    resolved, _graph, candidates = _resolve(client, object_id)
    if resolved is None:
        return _unresolved(object_id, candidates)
    detail = object_detail(client, resolved)
    if detail is None:
        return _unresolved(object_id, candidates)
    detail["found"] = True
    detail["evidence_count"] = evidence_count(client, resolved)
    return detail


def neighbors(settings: McpSettings, object_id: str) -> dict:
    """Connected objects grouped by relationship predicate."""

    client = _client(settings)
    resolved, graph, candidates = _resolve(client, object_id)
    if resolved is None:
        return _unresolved(object_id, candidates)
    return {
        "found": True,
        "object_id": resolved,
        "label": network.label_of(graph, resolved),
        "neighbors": network.neighbors(graph, resolved),
    }


def impact(settings: McpSettings, object_id: str) -> dict:
    """What may be affected by a change: neighbours grouped by object type."""

    client = _client(settings)
    resolved, graph, candidates = _resolve(client, object_id)
    if resolved is None:
        return _unresolved(object_id, candidates)
    grouped = network.impact(graph, resolved)
    return {
        "found": True,
        "object_id": resolved,
        "label": network.label_of(graph, resolved),
        "total": sum(len(v) for v in grouped.values()),
        "impact": grouped,
    }


def find_path(settings: McpSettings, source: str, target: str) -> dict:
    """Shortest relationship path between two objects (direction-agnostic)."""

    client = _client(settings)
    graph = network.build_digraph(client)
    src, src_cands = _resolve_id(graph, source)
    if src is None:
        return _unresolved(source, src_cands)
    tgt, tgt_cands = _resolve_id(graph, target)
    if tgt is None:
        return _unresolved(target, tgt_cands)
    hops = network.shortest_path(graph, src, tgt)
    if hops is None:
        return {
            "found": False,
            "source": src,
            "target": tgt,
            "message": (
                f"No path between {network.label_of(graph, src)} and "
                f"{network.label_of(graph, tgt)}."
            ),
        }
    return {
        "found": True,
        "source": src,
        "target": tgt,
        "hop_count": len(hops),
        "path": hops,
    }


def evidence_for_object(settings: McpSettings, object_id: str) -> dict:
    """Supporting evidence quotes (artifact, quote, confidence) for an object."""

    client = _client(settings)
    resolved, _graph, candidates = _resolve(client, object_id)
    if resolved is None:
        return _unresolved(object_id, candidates)
    items = evidence_for(client, resolved)
    return {"found": True, "object_id": resolved, "count": len(items), "evidence": items}


# -- discovery and batch-traversal tools (offline, no LLM) -------------------

def graph_schema(settings: McpSettings) -> dict:
    """Types and predicates present in the graph — the agent's starting map."""

    graph = network.build_digraph(_client(settings))
    types = network.object_types(graph)
    predicates = sorted({d["predicate"] for _, _, d in graph.edges(data=True)})
    return {
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "types": types,
        "predicates": predicates,
    }


def list_objects(
    settings: McpSettings,
    type_filter: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Paginated object list, optionally filtered by type."""

    graph = network.build_digraph(_client(settings))
    all_nodes = network.nodes_by_type(graph, type_filter or None)
    page = all_nodes[offset : offset + limit]
    return {
        "total": len(all_nodes),
        "limit": limit,
        "offset": offset,
        "type_filter": type_filter or None,
        "objects": page,
    }


def domains(settings: McpSettings) -> dict:
    """Knowledge domain overview: per-type object counts and most central nodes."""

    from ..graph.domains import analyze_domains

    return {"domains": analyze_domains(_client(settings))}


def get_subgraph(settings: McpSettings, object_id: str, depth: int = 2) -> dict:
    """All nodes and edges within *depth* hops of an object (batch neighbourhood)."""

    client = _client(settings)
    resolved, graph, candidates = _resolve(client, object_id)
    if resolved is None:
        return _unresolved(object_id, candidates)
    depth = max(1, min(depth, 4))
    sub = network.ego_subgraph(graph, resolved, depth)
    return {
        "found": True,
        "object_id": resolved,
        "label": network.label_of(graph, resolved),
        "depth": depth,
        **(sub or {}),
    }


# -- LLM-backed tool (gated, graceful decline) --------------------------------

def ask(settings: McpSettings, question: str, depth: int = 2) -> dict:
    """Answer a question over the approved graph with citations and confidence.

    Graph retrieval is mandatory and nothing unapproved reaches the model. When
    GraphRAG is disabled, or no LLM provider/key is configured, the tool returns a
    structured ``available: false`` result instead of raising — mirroring the REST
    API's 501 behaviour so an agent can degrade gracefully to the graph-first
    tools above.
    """

    if not settings.enable_graphrag:
        return unavailable(
            "GraphRAG is disabled for this server. Restart with --enable-graphrag "
            "and a configured LLM provider, or use the graph-first tools."
        )

    config = load_llm_config(settings.llm_config)
    try:
        provider = build_provider(config)
    except LLMError as exc:
        return unavailable(f"GraphRAG unavailable: {exc}")

    depth = max(1, depth)
    client = _client(settings)
    assistant = GraphRAGAssistant(client, provider, depth=depth)
    answer = assistant.ask(question, depth=depth)
    # Price and persist token usage so MCP ``ask`` shows up in `catalog cost-report`.
    record_calls(
        settings.db_path,
        assistant.drain_usage(),
        operation="ask",
        provider_name=config.provider,
    )
    return answer_to_dict(answer)


# -- write tools (policy-gated, attributable, reversible) ---------------------

def _agent_policy(settings: McpSettings):
    return load_governance_config(settings.governance_config).agent_review


def approve_object(settings: McpSettings, object_id: str, note: str = "") -> dict:
    """Approve one PROPOSED object iff it passes the agent-review policy.

    Declines gracefully (``approved: false`` with a reason) when agent review is
    disabled, the object does not exist or is not PROPOSED, or it falls outside
    the configured confidence/evidence/type policy. The decision is tagged
    ``agent:<name>`` and is reversible.
    """

    policy = _agent_policy(settings)
    if not (settings.enable_agent_review and policy.enabled):
        return {"approved": False, "reason": "agent review is disabled on this server"}

    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        row = know_repo.get_object(conn, object_id)
        if row is None:
            return {"approved": False, "object_id": object_id, "reason": "no such object"}
        if row["status"] != ReviewState.PROPOSED.value:
            return {
                "approved": False,
                "object_id": object_id,
                "reason": f"object is {row['status']}, not PROPOSED",
            }
        ok, reason = agent_review.object_eligible(conn, row, policy)
    if not ok:
        return {"approved": False, "object_id": object_id, "reason": reason}

    reviewer = agent_reviewer(policy.agent_name)
    changed = gov_service.approve_object(
        settings.db_path, object_id, reviewer=reviewer, note=note
    )
    return {"approved": bool(changed), "object_id": object_id, "reviewer": reviewer}


def approve_relationship(settings: McpSettings, relationship_id: int, note: str = "") -> dict:
    """Approve one PROPOSED relationship iff it passes the agent-review policy."""

    policy = _agent_policy(settings)
    if not (settings.enable_agent_review and policy.enabled):
        return {"approved": False, "reason": "agent review is disabled on this server"}

    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        row = know_repo.get_relationship(conn, relationship_id)
        if row is None:
            return {
                "approved": False,
                "relationship_id": relationship_id,
                "reason": "no such relationship",
            }
        if row["review_status"] != ReviewState.PROPOSED.value:
            return {
                "approved": False,
                "relationship_id": relationship_id,
                "reason": f"relationship is {row['review_status']}, not PROPOSED",
            }
        ok, reason = agent_review.relationship_eligible(conn, row, policy)
    if not ok:
        return {"approved": False, "relationship_id": relationship_id, "reason": reason}

    reviewer = agent_reviewer(policy.agent_name)
    changed = know_service.review_relationship(
        settings.db_path,
        relationship_id,
        ReviewState.APPROVED.value,
        reviewer=reviewer,
        note=note,
    )
    return {
        "approved": bool(changed),
        "relationship_id": relationship_id,
        "reviewer": reviewer,
    }


def flag_object(settings: McpSettings, object_id: str, note: str = "") -> dict:
    """Escalate an uncertain object to the human review queue (NEEDS_ATTENTION).

    The safe complement to approval: when the agent is unsure, it can flag rather
    than approve, leaving the decision to a human. Not bounded by the confidence
    policy (flagging never trusts anything), only by ``enable_agent_review``.
    """

    if not settings.enable_agent_review:
        return {"flagged": False, "reason": "agent review is disabled on this server"}
    reviewer = agent_reviewer(_agent_policy(settings).agent_name)
    changed = gov_service.flag_object(
        settings.db_path, object_id, reviewer=reviewer, note=note
    )
    if not changed:
        return {"flagged": False, "object_id": object_id, "reason": "no such object"}
    return {"flagged": True, "object_id": object_id, "reviewer": reviewer}


__all__ = [
    "search_knowledge",
    "get_object",
    "neighbors",
    "impact",
    "find_path",
    "evidence_for_object",
    "graph_schema",
    "list_objects",
    "domains",
    "get_subgraph",
    "ask",
    "approve_object",
    "approve_relationship",
    "flag_object",
]
