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
    def ask(question: str, depth: int = 2) -> dict:
        """Answer a natural-language question over the approved knowledge graph.

        Retrieval is graph-first and every claim is cited; the result carries the
        objects, relationships, evidence, and a confidence band used to answer.
        Returns ``available: false`` if the LLM-backed assistant is not configured.
        """
        return tools.ask(settings, question, depth)

    return server


def run(
    *,
    db_path: str = "data/catalog.sqlite",
    queries_dir: str = "queries",
    llm_config: str = "config/llm.yml",
    enable_graphrag: bool = True,
) -> None:
    """Start the MCP grounding server on stdio (blocks until the client exits)."""

    settings = McpSettings(
        db_path=db_path,
        queries_dir=queries_dir,
        llm_config=llm_config,
        enable_graphrag=enable_graphrag,
    )
    server = build_server(settings)
    server.run()  # stdio transport is FastMCP's default


__all__ = ["build_server", "run"]
