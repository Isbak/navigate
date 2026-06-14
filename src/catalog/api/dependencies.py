"""FastAPI dependencies: settings, database connections, and API-key auth.

Settings live on ``app.state`` (set by :func:`catalog.api.app.create_app`) so a
single configured application instance is shared by every request. The database
dependency opens a short-lived SQLite connection per request and always closes
it; the schema is ensured once at startup.
"""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, Request

from ..db import connect
from .config import ApiSettings
from .errors import unauthorized
from .jobs_context import build_job_context  # re-exported for routes

__all__ = [
    "get_settings",
    "get_db",
    "require_api_key",
    "build_job_context",
]


def get_settings(request: Request) -> ApiSettings:
    """Return the application's resolved settings."""

    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Yield a per-request SQLite connection (read/write), closing it afterwards."""

    settings: ApiSettings = request.app.state.settings
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def require_api_key(
    request: Request, settings: ApiSettings = Depends(get_settings)
) -> None:
    """Enforce ``Authorization: Bearer <token>`` when an API key is required.

    Disabled by default (safe local-first default). When enabled but no key is
    configured in the environment, every request is rejected rather than silently
    allowing access.
    """

    if not settings.require_api_key:
        return
    expected = settings.api_key
    if not expected:
        raise unauthorized(
            "API key authentication is enabled but no key is configured.",
            api_key_env=settings.api_key_env,
        )
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != expected:
        raise unauthorized("Missing or invalid API key.")
