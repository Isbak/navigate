"""Shared HTTP transport for connector implementations.

Uses ``requests`` (already a core dependency) with bounded exponential back-off
on transient failures: HTTP 429/5xx and network errors. Permanent 4xx responses
raise immediately so a bad URL or revoked token fails fast.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

from .base import ConnectorAuthError, ConnectorError

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF = 1.0   # seconds for the first retry; doubled each attempt
MAX_BACKOFF = 60.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _retry_after(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    label: str,
    timeout: float = 30.0,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> requests.Response:
    attempt = 0
    while True:
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= max_retries:
                raise ConnectorError(f"{label}: network failure: {exc}") from exc
            delay: float = min(MAX_BACKOFF, backoff * (2 ** attempt))
        else:
            if resp.status_code == 401:
                raise ConnectorAuthError(
                    f"{label}: authentication failed (HTTP 401). Check credentials."
                )
            if resp.status_code == 403:
                raise ConnectorAuthError(
                    f"{label}: access forbidden (HTTP 403). Check permissions."
                )
            if resp.status_code not in RETRYABLE_STATUS:
                resp.raise_for_status()
                return resp
            if attempt >= max_retries:
                raise ConnectorError(
                    f"{label}: HTTP {resp.status_code} after {max_retries} retries"
                )
            delay = _retry_after(resp) or min(MAX_BACKOFF, backoff * (2 ** attempt))

        attempt += 1
        LOGGER.warning(
            "%s transient failure (attempt %d/%d); retrying in %.1fs",
            label, attempt, max_retries, delay,
        )
        sleep(delay)


def get_json(
    session: requests.Session,
    url: str,
    *,
    label: str = "GET",
    params: dict | None = None,
    timeout: float = 30.0,
    **kwargs: Any,
) -> dict:
    resp = _request(session, "GET", url, label=label, timeout=timeout, params=params, **kwargs)
    return resp.json()


def get_bytes(
    session: requests.Session,
    url: str,
    *,
    label: str = "GET",
    params: dict | None = None,
    timeout: float = 120.0,
    **kwargs: Any,
) -> bytes:
    resp = _request(session, "GET", url, label=label, timeout=timeout, params=params, **kwargs)
    return resp.content


def post_json(
    session: requests.Session,
    url: str,
    *,
    label: str = "POST",
    timeout: float = 30.0,
    **kwargs: Any,
) -> dict:
    resp = _request(session, "POST", url, label=label, timeout=timeout, **kwargs)
    return resp.json()
