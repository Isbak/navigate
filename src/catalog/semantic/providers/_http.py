"""Shared HTTP transport for the stdlib-based LLM providers.

Every provider POSTs JSON and reads a JSON envelope back. This helper performs
that single round-trip with bounded exponential backoff on *transient* failures
- HTTP 429 (rate limited), 5xx (server-side), and connection/timeout errors -
while letting permanent failures (4xx other than 429, malformed JSON) fail fast.

Centralizing it means OpenAI, Claude, and Ollama share identical retry
behaviour: a single rate-limit blip or upstream hiccup no longer aborts an
extraction or classification run that may already have spent tokens.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from urllib import error, request

from .base import LLMError

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF = 0.5  # seconds for the first retry; doubled each attempt
MAX_BACKOFF = 30.0
# 429 = rate limited; 5xx = server-side; 529 = Anthropic "overloaded".
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 529})


def _retry_after(exc: error.HTTPError) -> float | None:
    """Return the server-requested delay from a ``Retry-After`` header, if any.

    Only the numeric-seconds form is honoured; the HTTP-date form falls back to
    the computed exponential backoff (returns ``None``).
    """

    raw = exc.headers.get("Retry-After") if exc.headers else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def request_json(
    req: request.Request,
    *,
    label: str,
    timeout: float,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict, float]:
    """POST ``req`` and return ``(parsed_body, latency_ms)``.

    Retries transient failures up to ``max_retries`` times with exponential
    backoff (honouring ``Retry-After`` on 429s). Raises :class:`LLMError` on a
    permanent failure or once retries are exhausted. ``latency_ms`` measures only
    the successful round-trip, not the backoff waits, so usage provenance stays
    meaningful. ``sleep`` is injectable so tests need not wait in real time.
    """

    attempt = 0
    while True:
        try:
            started = time.perf_counter()
            with request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            latency_ms = (time.perf_counter() - started) * 1000
            return body, latency_ms
        except error.HTTPError as exc:  # subclass of URLError - must come first
            if exc.code not in RETRYABLE_STATUS or attempt >= max_retries:
                raise LLMError(
                    f"{label} request failed: HTTP {exc.code} {exc.reason}"
                ) from exc
            delay = _retry_after(exc)
            if delay is None:
                delay = min(MAX_BACKOFF, backoff * (2**attempt))
        except (error.URLError, TimeoutError, OSError) as exc:
            if attempt >= max_retries:
                raise LLMError(f"{label} request failed: {exc}") from exc
            delay = min(MAX_BACKOFF, backoff * (2**attempt))
        except json.JSONDecodeError as exc:
            raise LLMError(f"{label} returned invalid JSON envelope: {exc}") from exc

        attempt += 1
        LOGGER.warning(
            "%s request transient failure (attempt %d/%d); retrying in %.1fs",
            label,
            attempt,
            max_retries,
            delay,
        )
        sleep(delay)


__all__ = [
    "request_json",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_BACKOFF",
    "RETRYABLE_STATUS",
]
