"""CLI surface for the MCP grounding server (``catalog mcp``).

Wires the server into the ``catalog`` command line following the same
``add_*_parser`` / ``run_*`` pattern as ``catalog.graph.cli``. ``--db`` and
``--llm-config`` come from the global flags on the top-level parser.
"""

from __future__ import annotations

import argparse


def add_mcp_parser(sub: argparse._SubParsersAction) -> None:
    """Register the ``mcp`` command on the top-level subparsers."""

    mcp = sub.add_parser(
        "mcp",
        help="run the Model Context Protocol grounding server (stdio)",
    )
    mcp.add_argument("--queries-dir", default="queries")
    mcp.add_argument(
        "--enable-graphrag",
        dest="enable_graphrag",
        action="store_true",
        help="enable the LLM-backed 'ask' tool (default: enabled)",
    )
    mcp.add_argument(
        "--no-graphrag",
        dest="enable_graphrag",
        action="store_false",
        help="disable the LLM-backed 'ask' tool (graph-only, fully offline)",
    )
    mcp.set_defaults(enable_graphrag=True)


def run_mcp(args) -> None:
    """Dispatch the ``mcp`` command: start the stdio server."""

    from .server import run

    run(
        db_path=args.db,
        queries_dir=args.queries_dir,
        llm_config=args.llm_config,
        enable_graphrag=args.enable_graphrag,
    )


__all__ = ["add_mcp_parser", "run_mcp"]
