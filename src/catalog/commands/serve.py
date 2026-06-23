"""The ``api`` command: launch the local REST API server.

The heavy FastAPI/uvicorn import is deferred to call time so the rest of the CLI
stays fast to start.
"""

from __future__ import annotations

import argparse


def _cmd_api(args: argparse.Namespace) -> None:
    from ..api.server import run as run_api

    run_api(
        host=args.host,
        port=args.port,
        reload=args.reload,
        config_path=args.api_config,
        db_path=args.db,
        cache_dir=args.cache,
    )


def register(sub: argparse._SubParsersAction) -> None:
    api = sub.add_parser("api", help="run the local REST API server")
    api.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    api.add_argument("--port", type=int, default=None, help="bind port (default 8000)")
    api.add_argument("--reload", dest="reload", action="store_true", help="enable auto-reload")
    api.add_argument("--no-reload", dest="reload", action="store_false", help="disable auto-reload")
    api.add_argument("--api-config", default="config/api.yml")
    api.set_defaults(reload=None, func=_cmd_api)


__all__ = ["register"]
