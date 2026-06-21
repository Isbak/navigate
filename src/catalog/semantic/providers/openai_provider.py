"""OpenAI LLM provider.

Uses the Chat Completions HTTP API via the standard library so the package has
no hard dependency on the ``openai`` SDK. The API key is read from the
``OPENAI_API_KEY`` environment variable and never persisted.
"""

from __future__ import annotations

import json
import logging
import os
import time
from urllib import error, request

from catalog.cost.usage import Usage
from catalog.env import load_dotenv

from .base import BaseLLMProvider, LLMError

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT = 120
API_KEY_ENV = "OPENAI_API_KEY"


class OpenAIProvider(BaseLLMProvider):
    """Generate completions from an OpenAI chat model (e.g. ``gpt-5.5``)."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        api_key: str | None = None,
        api_key_env: str = API_KEY_ENV,
    ) -> None:
        super().__init__(model)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        load_dotenv()
        self.api_key_env = api_key_env
        self._api_key = api_key or os.environ.get(api_key_env)

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self._last_usage = None
        if not self._api_key:
            raise LLMError(
                f"OpenAI API key not set; export {self.api_key_env} or pass api_key"
            )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            # Request a JSON object so parsing downstream is reliable.
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"OpenAI request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"OpenAI returned invalid JSON envelope: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected OpenAI response shape: {exc}") from exc

        usage = body.get("usage") or {}
        self._last_usage = Usage(
            model=self.model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=latency_ms,
        )
        return text


__all__ = ["OpenAIProvider", "DEFAULT_BASE_URL", "DEFAULT_TIMEOUT", "API_KEY_ENV"]
