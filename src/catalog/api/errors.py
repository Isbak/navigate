"""Consistent error model for the REST API.

Every error the API returns has the same shape::

    {"error": "not_found", "message": "Artifact not found", "details": {}}

:class:`ApiError` is raised from route/dependency code; the handlers registered
in :mod:`catalog.api.app` turn it - and FastAPI's own HTTP/validation errors -
into that shape so clients never have to special-case error formats.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An error with a stable machine-readable code and HTTP status."""

    def __init__(
        self,
        *,
        status_code: int,
        error: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.error, "message": self.message, "details": self.details}


def not_found(message: str, **details: Any) -> ApiError:
    return ApiError(status_code=404, error="not_found", message=message, details=details)


def bad_request(message: str, **details: Any) -> ApiError:
    return ApiError(status_code=400, error="bad_request", message=message, details=details)


def unauthorized(message: str, **details: Any) -> ApiError:
    return ApiError(status_code=401, error="unauthorized", message=message, details=details)


def not_implemented(message: str, **details: Any) -> ApiError:
    return ApiError(
        status_code=501, error="not_implemented", message=message, details=details
    )


__all__ = [
    "ApiError",
    "not_found",
    "bad_request",
    "unauthorized",
    "not_implemented",
]
