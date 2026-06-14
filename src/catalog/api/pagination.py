"""Shared pagination helpers for list endpoints.

All list endpoints accept ``limit``/``offset`` and return a uniform envelope
(``items``/``limit``/``offset``/``total``). The query parameters are validated
here once so every route applies the same bounds.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


@dataclass(frozen=True)
class Pagination:
    """Validated pagination window."""

    limit: int = DEFAULT_LIMIT
    offset: int = 0


def pagination_params(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> Pagination:
    """FastAPI dependency that yields a validated :class:`Pagination`."""

    return Pagination(limit=limit, offset=offset)


__all__ = ["Pagination", "pagination_params", "DEFAULT_LIMIT", "MAX_LIMIT"]
