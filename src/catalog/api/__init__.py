"""The Navigate REST API.

A thin, local-first HTTP layer over the existing Navigate knowledge platform.
Route handlers are intentionally minimal: they validate input, call the existing
service/repository layer, and serialize the result. No business logic and no raw
SQL live in the routes - the API is a contract, not a second implementation.

The application is built by :func:`catalog.api.app.create_app` and run locally
via ``catalog api`` (or the ``navigate api`` alias).
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
