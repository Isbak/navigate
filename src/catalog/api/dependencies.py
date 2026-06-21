"""FastAPI dependencies: settings, database connections, and API-key auth.

Settings live on ``app.state`` (set by :func:`catalog.api.app.create_app`) so a
single configured application instance is shared by every request. The database
dependency opens a short-lived SQLite connection per request and always closes
it; the schema is ensured once at startup.
"""

from __future__ import annotations

import hmac
import sqlite3
from typing import Iterator

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..db import connect
from .config import ApiSettings
from .errors import unauthorized
from .jobs_context import build_job_context  # re-exported for routes

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Optional Bearer token used when require_api_key is enabled.",
)

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
    settings: ApiSettings = Depends(get_settings),
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
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
    token = credentials.credentials.strip() if credentials else ""
    # Constant-time comparison so a rejected request cannot leak how many leading
    # characters of the key were correct via response timing.
    if not hmac.compare_digest(token, expected):
        raise unauthorized("Missing or invalid API key.")
