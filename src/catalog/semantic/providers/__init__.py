"""LLM provider abstraction.

Everything above this package depends only on :class:`BaseLLMProvider`, so the
classification service is agnostic to whether completions come from a local
Ollama model or a hosted OpenAI model. New backends are added by writing a
subclass and registering it in :data:`_PROVIDERS`.
"""

from __future__ import annotations

from typing import Callable

from ..config import LLMConfig
from .base import BaseLLMProvider, LLMError
from .claude_provider import ClaudeProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider


def _build_ollama(config: LLMConfig) -> BaseLLMProvider:
    opts = config.options
    return OllamaProvider(
        config.model,
        host=opts.get("host", "http://localhost:11434"),
        timeout=int(opts.get("timeout", 120)),
    )


def _build_claude(config: LLMConfig) -> BaseLLMProvider:
    opts = config.options
    return ClaudeProvider(
        config.model,
        base_url=opts.get("base_url", "https://api.anthropic.com/v1"),
        timeout=int(opts.get("timeout", 120)),
        max_tokens=int(opts.get("max_tokens", 4096)),
        anthropic_version=opts.get("anthropic_version", "2023-06-01"),
        api_key_env=opts.get("api_key_env", "ANTHROPIC_API_KEY"),
        cache_system_prompt=bool(opts.get("prompt_cache", True)),
    )


def _build_openai(config: LLMConfig) -> BaseLLMProvider:
    opts = config.options
    return OpenAIProvider(
        config.model,
        base_url=opts.get("base_url", "https://api.openai.com/v1"),
        timeout=int(opts.get("timeout", 120)),
        api_key_env=opts.get("api_key_env", "OPENAI_API_KEY"),
    )


# Registry of known providers. Adding a backend is a one-line change here plus a
# BaseLLMProvider subclass - no edits to the service, prompts, or parser.
_PROVIDERS: dict[str, Callable[[LLMConfig], BaseLLMProvider]] = {
    "claude": _build_claude,
    "ollama": _build_ollama,
    "openai": _build_openai,
}


def available_providers() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDERS))


def build_provider(config: LLMConfig) -> BaseLLMProvider:
    """Construct the provider named by ``config.provider``.

    Raises :class:`LLMError` for an unknown provider so callers get a clear,
    actionable message instead of a ``KeyError``.
    """

    factory = _PROVIDERS.get(config.provider)
    if factory is None:
        raise LLMError(
            f"Unknown LLM provider {config.provider!r}; "
            f"available: {', '.join(available_providers())}"
        )
    return factory(config)


__all__ = [
    "BaseLLMProvider",
    "LLMError",
    "ClaudeProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "build_provider",
    "available_providers",
]
