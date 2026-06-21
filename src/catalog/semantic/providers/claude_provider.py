"""Claude LLM provider.

Uses Anthropic's Messages HTTP API via the standard library so the package has
no hard dependency on the ``anthropic`` SDK. The API key is read from the
``ANTHROPIC_API_KEY`` environment variable and never persisted.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from urllib import error, request

from catalog.cost.usage import Usage
from catalog.env import load_dotenv

from .base import BaseLLMProvider, LLMError

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_TOKENS = 4096
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
API_KEY_ENV = "ANTHROPIC_API_KEY"


class ClaudeProvider(BaseLLMProvider):
    """Generate completions from an Anthropic Claude model."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
        api_key: str | None = None,
        api_key_env: str = API_KEY_ENV,
    ) -> None:
        super().__init__(model)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.anthropic_version = anthropic_version
        load_dotenv()
        self.api_key_env = api_key_env
        self._api_key = api_key or os.environ.get(api_key_env)

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: list[bytes] | None = None,
        image_media_type: str = "image/png",
    ) -> str:
        self._last_usage = None
        if not self._api_key:
            raise LLMError(
                f"Anthropic API key not set; export {self.api_key_env} or pass api_key"
            )

        if images:
            content: list[dict] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_media_type,
                        "data": base64.b64encode(image).decode("ascii"),
                    },
                }
                for image in images
            ]
            content.append({"type": "text", "text": prompt})
        else:
            content = prompt

        payload: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            payload["system"] = system

        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": self.anthropic_version,
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"Claude request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"Claude returned invalid JSON envelope: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            content = body["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Claude response shape: {exc}") from exc

        text_parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if not text_parts:
            raise LLMError("Claude response missing text content")

        usage = body.get("usage") or {}
        self._last_usage = Usage(
            model=self.model,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
            latency_ms=latency_ms,
        )
        return "".join(text_parts)


__all__ = [
    "ClaudeProvider",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_ANTHROPIC_VERSION",
    "API_KEY_ENV",
]
