"""Ollama LLM provider.

Talks to a local Ollama server's HTTP API using only the standard library, so
no extra dependency is required to run a fully local, offline classification.
"""

from __future__ import annotations

import json
import logging
import time
from urllib import error, request

from catalog.cost.usage import Usage

from .base import BaseLLMProvider, LLMError

LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 120


class OllamaProvider(BaseLLMProvider):
    """Generate completions from a local Ollama model (e.g. ``qwen3:14b``)."""

    def __init__(
        self,
        model: str,
        *,
        host: str = DEFAULT_HOST,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(model)
        self.host = host.rstrip("/")
        self.timeout = timeout

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self._last_usage = None
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            # Ask Ollama to constrain output to JSON where the model supports it.
            "format": "json",
        }
        if system:
            payload["system"] = system

        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.host}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama returned invalid JSON envelope: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        response = body.get("response")
        if not isinstance(response, str):
            raise LLMError("Ollama response missing 'response' field")

        self._last_usage = Usage(
            model=self.model,
            input_tokens=int(body.get("prompt_eval_count", 0) or 0),
            output_tokens=int(body.get("eval_count", 0) or 0),
            latency_ms=latency_ms,
        )
        return response


__all__ = ["OllamaProvider", "DEFAULT_HOST", "DEFAULT_TIMEOUT"]
