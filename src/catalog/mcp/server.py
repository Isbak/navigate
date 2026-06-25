"""FastMCP server that publishes the catalog's grounding tools over stdio.

The official ``mcp`` SDK is an optional dependency (``pip install '.[mcp]'``),
imported lazily here so the rest of the package — and the pure tool functions in
:mod:`catalog.mcp.tools` — never require it. ``build_server`` registers thin
wrappers that delegate to those tool functions; FastMCP derives each tool's JSON
schema from the wrapper's type hints and docstring.
"""

from __future__ import annotations

from typing import Any

from . import tools
from .config import McpSettings

_MCP_MISSING = (
    "The MCP server needs the optional 'mcp' package.\n"
    "Install it with:  pip install '.[mcp]'"
)


def _import_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised via run() message
        raise SystemExit(_MCP_MISSING) from exc
    return FastMCP


def build_server(settings: McpSettings) -> Any:
    """Construct a ``FastMCP`` instance with every grounding tool registered."""

    FastMCP = _import_fastmcp()
    server = FastMCP("navigate-knowledge")

    @server.tool()
    def search_knowledge(term: str) -> dict:
        """Search the approved knowledge graph by name or description.

        Returns matching knowledge objects with their id, label, type, and
        description. Use this first to find the object ids the other tools take.
        """
        return tools.search_knowledge(settings, term)

    @server.tool()
    def get_object(object_id: str) -> dict:
        """Get full detail for one knowledge object (accepts an id or a name):
        type, description, confidence, and evidence count.
        """
        return tools.get_object(settings, object_id)

    @server.tool()
    def neighbors(object_id: str) -> dict:
        """List objects directly connected to this one, grouped by relationship."""
        return tools.neighbors(settings, object_id)

    @server.tool()
    def impact(object_id: str) -> dict:
        """Show what a change to this object may affect (neighbours by type)."""
        return tools.impact(settings, object_id)

    @server.tool()
    def find_path(source: str, target: str) -> dict:
        """Find the shortest relationship path between two knowledge objects."""
        return tools.find_path(settings, source, target)

    @server.tool()
    def evidence_for(object_id: str) -> dict:
        """Return the supporting evidence quotes that back a knowledge object."""
        return tools.evidence_for_object(settings, object_id)

    @server.tool()
    def graph_schema() -> dict:
        """Return the types and predicates that exist in the knowledge graph.

        Use this first to discover what kinds of objects and relationships the
        graph contains before searching or traversing. Returns ``types`` (each with
        a ``count``), ``predicates``, and total ``node_count`` / ``edge_count``.
        """
        return tools.graph_schema(settings)

    @server.tool()
    def list_objects(type_filter: str = "", limit: int = 50, offset: int = 0) -> dict:
        """List approved knowledge objects, optionally filtered by type.

        Pass a ``type_filter`` (e.g. ``"Capability"``, ``"Decision"``) to enumerate
        objects within a domain.  Use ``limit`` and ``offset`` to page through large
        result sets.  Each object carries ``id``, ``label``, ``type``, and
        ``confidence``.
        """
        return tools.list_objects(settings, type_filter, limit, offset)

    @server.tool()
    def domains() -> dict:
        """Return a summary of each knowledge domain (object type).

        For every type in the graph, reports ``object_count``,
        ``relationship_count``, and the top 5 most-central nodes by degree.
        Useful for sizing the graph and identifying the best entry points.
        """
        return tools.domains(settings)

    @server.tool()
    def get_subgraph(object_id: str, depth: int = 2) -> dict:
        """Return all nodes and edges within *depth* hops of an object.

        Replaces multiple sequential ``neighbors`` calls when an agent needs to
        reason over a neighbourhood at once.  ``depth`` is capped at 4 to keep
        responses manageable.  Returns ``nodes`` and ``edges`` lists alongside
        the root object's ``label``.
        """
        return tools.get_subgraph(settings, object_id, depth)

    @server.tool()
    def ask(question: str, depth: int = 2) -> dict:
        """Answer a natural-language question over the approved knowledge graph.

        Retrieval is graph-first and every claim is cited; the result carries the
        objects, relationships, evidence, and a confidence band used to answer.
        Returns ``available: false`` if the LLM-backed assistant is not configured.
        """
        return tools.ask(settings, question, depth)

    if settings.enable_agent_review:
        _register_write_tools(server, settings)

    return server


def _register_write_tools(server: Any, settings: McpSettings) -> None:
    """Register the policy-gated write tools (only when agent review is enabled).

    These are the only tools that change the graph. Each stays inside the
    ``agent_review`` policy in ``config/governance.yml`` — confidence window,
    evidence, type/predicate allowlists — and every decision is tagged
    ``agent:<name>`` so a human can audit and revert it.
    """

    @server.tool()
    def approve_object(object_id: str, note: str = "") -> dict:
        """Approve one PROPOSED knowledge object if it passes the review policy.

        Returns ``approved: false`` with a reason when the object is missing, not
        PROPOSED, or outside the configured confidence/evidence/type policy.
        """
        return tools.approve_object(settings, object_id, note)

    @server.tool()
    def approve_relationship(relationship_id: int, note: str = "") -> dict:
        """Approve one PROPOSED relationship if it passes the review policy."""
        return tools.approve_relationship(settings, relationship_id, note)

    @server.tool()
    def flag_object(object_id: str, note: str = "") -> dict:
        """Escalate an uncertain object to the human review queue instead of approving."""
        return tools.flag_object(settings, object_id, note)


def run(
    *,
    db_path: str = "data/catalog.sqlite",
    queries_dir: str = "queries",
    llm_config: str = "config/llm.yml",
    governance_config: str = "config/governance.yml",
    enable_graphrag: bool = True,
    enable_agent_review: bool = False,
) -> None:
    """Start the MCP grounding server on stdio (blocks until the client exits)."""

    settings = McpSettings(
        db_path=db_path,
        queries_dir=queries_dir,
        llm_config=llm_config,
        governance_config=governance_config,
        enable_graphrag=enable_graphrag,
        enable_agent_review=enable_agent_review,
    )
    server = build_server(settings)
    server.run()  # stdio transport is FastMCP's default


__all__ = ["build_server", "run"]
