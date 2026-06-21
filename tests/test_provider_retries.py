"""Transient-retry behaviour shared by every stdlib LLM provider.

The providers route all HTTP through :func:`catalog.semantic.providers._http.request_json`,
so these tests exercise that helper directly (fast, no real sleeping) and then
confirm one provider wires its ``max_retries``/``retry_backoff`` through.
"""

import io
import json

import pytest

from catalog.semantic.config import LLMConfig
from catalog.semantic.providers import OpenAIProvider, build_provider
from catalog.semantic.providers._http import (
    DEFAULT_BACKOFF,
    DEFAULT_MAX_RETRIES,
    request_json,
)
from catalog.semantic.providers.base import LLMError


class _Req:
    """Minimal stand-in for urllib.request.Request (request_json only opens it)."""


def _http_error(code, *, reason="boom", retry_after=None):
    from urllib import error

    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return error.HTTPError(
        url="http://x", code=code, msg=reason, hdrs=headers, fp=None
    )


def _scripted_opener(outcomes, calls):
    """Return an opener that yields ``outcomes`` in order; raises if exhausted."""

    def _open(req, timeout=None):
        calls.append(timeout)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome)

    return _open


def test_request_json_retries_then_succeeds(monkeypatch):
    sleeps = []
    calls = []
    ok = json.dumps({"response": "ok"}).encode("utf-8")
    outcomes = [_http_error(503), _http_error(429), ok]
    monkeypatch.setattr(
        "catalog.semantic.providers._http.request.urlopen",
        _scripted_opener(outcomes, calls),
    )

    body, latency_ms = request_json(
        _Req(), label="Test", timeout=1, sleep=sleeps.append
    )

    assert body == {"response": "ok"}
    assert latency_ms >= 0
    assert len(calls) == 3  # two failures + the success
    # Exponential backoff: 0.5s then 1.0s before the third attempt.
    assert sleeps == [DEFAULT_BACKOFF, DEFAULT_BACKOFF * 2]


def test_request_json_honours_retry_after_header(monkeypatch):
    sleeps = []
    calls = []
    ok = json.dumps({"ok": True}).encode("utf-8")
    outcomes = [_http_error(429, retry_after="7"), ok]
    monkeypatch.setattr(
        "catalog.semantic.providers._http.request.urlopen",
        _scripted_opener(outcomes, calls),
    )

    request_json(_Req(), label="Test", timeout=1, sleep=sleeps.append)

    assert sleeps == [7.0]  # server-requested delay overrides computed backoff


def test_request_json_does_not_retry_4xx(monkeypatch):
    sleeps = []
    calls = []
    outcomes = [_http_error(400, reason="bad request")]
    monkeypatch.setattr(
        "catalog.semantic.providers._http.request.urlopen",
        _scripted_opener(outcomes, calls),
    )

    with pytest.raises(LLMError, match="HTTP 400"):
        request_json(_Req(), label="Test", timeout=1, sleep=sleeps.append)

    assert len(calls) == 1  # permanent error: tried once, never slept
    assert sleeps == []


def test_request_json_gives_up_after_max_retries(monkeypatch):
    sleeps = []
    calls = []
    outcomes = [_http_error(503)] * 10
    monkeypatch.setattr(
        "catalog.semantic.providers._http.request.urlopen",
        _scripted_opener(outcomes, calls),
    )

    with pytest.raises(LLMError, match="HTTP 503"):
        request_json(
            _Req(), label="Test", timeout=1, max_retries=2, sleep=sleeps.append
        )

    assert len(calls) == 3  # initial attempt + 2 retries
    assert len(sleeps) == 2


def test_provider_passes_retry_config_through_to_helper(monkeypatch):
    captured = {}

    def _fake(req, *, label, timeout, max_retries, backoff, **_):
        captured.update(max_retries=max_retries, backoff=backoff)
        return {"choices": [{"message": {"content": "{}"}}]}, 1.0

    monkeypatch.setattr(
        "catalog.semantic.providers.openai_provider.request_json", _fake
    )
    provider = OpenAIProvider(
        "gpt-5.5", api_key="k", max_retries=5, retry_backoff=2.5
    )
    provider.generate("hi")

    assert captured == {"max_retries": 5, "backoff": 2.5}


def test_build_provider_reads_retry_options():
    cfg = LLMConfig(
        provider="openai",
        model="gpt-5.5",
        options={"max_retries": 7, "retry_backoff": 1.5},
    )
    provider = build_provider(cfg)
    assert provider.max_retries == 7
    assert provider.retry_backoff == 1.5


def test_defaults_are_sane():
    assert DEFAULT_MAX_RETRIES >= 1
    assert DEFAULT_BACKOFF > 0
