"""Configuration for the semantic classification layer.

Reads ``config/llm.yml`` and exposes a small :class:`LLMConfig` that the
provider factory consumes. The loader is tolerant: a missing file falls back to
a local Ollama default so the layer is usable out of the box, and unknown keys
in a provider block are passed through as ``options`` so new providers can read
their own settings without changing this loader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_LLM_CONFIG_PATH = Path("config/llm.yml")

DEFAULT_PROVIDER = "claude"
DEFAULT_MODELS = {
    "ollama": "qwen3:14b",
    "openai": "gpt-5.5",
    "claude": "claude-sonnet-4-5",
}
DEFAULT_MAX_INPUT_CHARS = 12000
DEFAULT_CHUNK_OVERLAP = 500
DEFAULT_MAX_CHUNKS = 20


@dataclass(frozen=True)
class LLMConfig:
    """Resolved configuration for the active LLM provider.

    ``options`` holds the remaining keys from the selected provider's block
    (host, base_url, timeout, ...) so provider constructors can read what they
    need without this module knowing about every backend.

    ``max_input_chars`` is the size of one chunk; long documents are split into
    chunks of that size (with ``chunk_overlap`` characters of overlap) and
    classified chunk by chunk, up to ``max_chunks`` chunks.
    """

    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODELS[DEFAULT_PROVIDER]
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    max_chunks: int = DEFAULT_MAX_CHUNKS
    options: dict = field(default_factory=dict)


def load_llm_config(path: str | Path = DEFAULT_LLM_CONFIG_PATH) -> LLMConfig:
    config_path = Path(path)
    if not config_path.exists():
        return LLMConfig()

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    provider = str(raw.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER
    block = dict(raw.get(provider, {}) or {})
    model = str(block.pop("model", DEFAULT_MODELS.get(provider, ""))).strip()
    if not model:
        raise ValueError(
            f"No model configured for provider {provider!r} in {config_path}"
        )

    max_input_chars = int(raw.get("max_input_chars", DEFAULT_MAX_INPUT_CHARS))
    chunk_overlap = int(raw.get("chunk_overlap", DEFAULT_CHUNK_OVERLAP))
    max_chunks = int(raw.get("max_chunks", DEFAULT_MAX_CHUNKS))

    return LLMConfig(
        provider=provider,
        model=model,
        max_input_chars=max_input_chars,
        chunk_overlap=chunk_overlap,
        max_chunks=max_chunks,
        options=block,
    )


__all__ = [
    "LLMConfig",
    "load_llm_config",
    "DEFAULT_LLM_CONFIG_PATH",
    "DEFAULT_PROVIDER",
    "DEFAULT_MODELS",
    "DEFAULT_MAX_INPUT_CHARS",
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_MAX_CHUNKS",
]
