"""Top-level ``catalog`` / ``navigate`` CLI.

This module is deliberately thin: the shared global flags and dispatch live here,
while each group of commands lives in its own module under
:mod:`catalog.commands` (registered via ``register_all``) or in a sub-CLI
(``graph``, ``governance``, ``compliance``, ``graphrag``, ``mcp``). Command
modules attach their handler with ``parser.set_defaults(func=...)``, so dispatch
is a single ``args.func(args)`` call.
"""

from __future__ import annotations

import argparse
import sys

from .commands import register_all
from .commands._common import configure_logging
from .compliance.cli import add_compliance_parser, run_compliance
from .db import DatabaseNotWritableError
from .governance.cli import add_governance_parser, run_governance
from .graph.cli import add_graph_parser, run_graph
from .graphrag.cli import add_graphrag_parsers, run_graphrag
from .mcp.cli import add_mcp_parser, run_mcp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="catalog")
    parser.add_argument("--db", default="data/catalog.sqlite")
    parser.add_argument("--config", default="config/sources.yml")
    parser.add_argument("--cache", default="cache")
    parser.add_argument("--link-config", default="config/link_patterns.yml")
    parser.add_argument("--llm-config", default="config/llm.yml")
    parser.add_argument("--extract-config", default="config/extraction.yml")
    parser.add_argument("--jena-config", default="config/jena.yml")
    parser.add_argument("--governance-config", default="config/governance.yml")
    parser.add_argument("--compliance-config", default="config/compliance.yml")
    parser.add_argument("--performance-config", default="config/performance.yml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    # Flat commands grouped into modules under catalog.commands.
    register_all(sub)

    # Sub-CLIs that own a nested command group and their own dispatch.
    add_graph_parser(sub)
    add_graphrag_parsers(sub)
    add_governance_parser(sub)
    add_compliance_parser(sub)
    add_mcp_parser(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    try:
        return _dispatch(args)
    except DatabaseNotWritableError as exc:
        # Surface the actionable cause instead of an opaque SQLite traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    # Command modules attach their handler via set_defaults(func=...).
    handler = getattr(args, "func", None)
    if handler is not None:
        return handler(args) or 0

    # Sub-CLIs with nested commands keep their own dispatchers.
    if args.command == "graph":
        run_graph(args)
    elif args.command == "governance":
        run_governance(args)
    elif args.command == "compliance":
        run_compliance(args)
    elif args.command == "mcp":
        run_mcp(args)
    elif args.command in {"ask", "explain", "impact", "compare", "path-reason"}:
        run_graphrag(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
