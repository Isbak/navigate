"""Settings for the MCP grounding server.

A small frozen bundle of the paths and toggles the tools need, so tool functions
take one ``settings`` argument instead of a long parameter list. Mirrors the
spirit of :class:`catalog.api.config.ApiSettings`, but minimal — the MCP server
is a local stdio subprocess, not a network service.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class McpSettings:
    """Resolved configuration for one MCP server process."""

    db_path: str = "data/catalog.sqlite"
    queries_dir: str = "queries"
    llm_config: str = "config/llm.yml"
    # The graph-first tools are always available and fully offline. ``ask`` is
    # the one tool that calls an external LLM; when disabled (or when no provider
    # /key is configured) it declines gracefully instead of raising.
    enable_graphrag: bool = True


__all__ = ["McpSettings"]
