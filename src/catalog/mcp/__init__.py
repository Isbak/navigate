"""Model Context Protocol (MCP) grounding server for the knowledge catalog.

Exposes the approved knowledge graph and the GraphRAG assistant as MCP tools so
that an external agent (Claude Code, Claude Desktop, any MCP client) can ground
its reasoning in cited, confidence-scored knowledge instead of free-associating.

The package is a *thin adapter*: every tool delegates to the same service and
helper layer the CLI (``catalog graph ...`` / ``catalog ask``) and the REST API
already use. It contains no business logic and no SQL of its own.

The ``mcp`` runtime dependency is optional (``pip install '.[mcp]'``) and is
imported lazily inside :func:`catalog.mcp.server.run`, so importing this package
never requires it. The pure tool functions in :mod:`catalog.mcp.tools` work
without it, which keeps them unit-testable offline.
"""

from __future__ import annotations

from .cli import add_mcp_parser, run_mcp
from .config import McpSettings

__all__ = ["add_mcp_parser", "run_mcp", "McpSettings"]
