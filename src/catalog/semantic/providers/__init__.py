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
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider


def _build_ollama(config: LLMConfig) -> BaseLLMProvider:
    opts = config.options
    return OllamaProvider(
        config.model,
        host=opts.get("host", "http://localhost:11434"),
        timeout=int(opts.get("timeout", 120)),
    )


def _build_openai(config: LLMConfig) -> BaseLLMProvider:
    opts = config.options
    return OpenAIProvider(
        config.model,
        base_url=opts.get("base_url", "https://api.openai.com/v1"),
        timeout=int(opts.get("timeout", 120)),
    )


# Registry of known providers. Adding a backend is a one-line change here plus a
# BaseLLMProvider subclass - no edits to the service, prompts, or parser.
_PROVIDERS: dict[str, Callable[[LLMConfig], BaseLLMProvider]] = {
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
    "OllamaProvider",
    "OpenAIProvider",
    "build_provider",
    "available_providers",
]
