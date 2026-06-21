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
class RoutingConfig:
    """Adaptive model-routing settings (the ``routing`` block of ``llm.yml``).

    When ``enabled``, simple documents are classified with ``fast_model`` and
    complex ones (or fast-model results below ``escalate_below_confidence``) with
    ``deep_model``. Both models must belong to the active provider. The defaults
    here keep routing *off* so a missing block, or any non-Claude single-model
    setup, behaves exactly as before.
    """

    enabled: bool = False
    fast_model: str = ""
    deep_model: str = ""
    complexity_threshold: float = 0.5
    escalate_below_confidence: float = 0.6
    fast_max_chunks: int = 6


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
    routing: RoutingConfig = field(default_factory=RoutingConfig)
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
    routing = _parse_routing(raw.get("routing"))

    return LLMConfig(
        provider=provider,
        model=model,
        max_input_chars=max_input_chars,
        chunk_overlap=chunk_overlap,
        max_chunks=max_chunks,
        routing=routing,
        options=block,
    )


def _parse_routing(raw: object) -> RoutingConfig:
    """Parse the optional ``routing`` block, falling back to disabled routing."""

    if not isinstance(raw, dict):
        return RoutingConfig()
    defaults = RoutingConfig()
    return RoutingConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        fast_model=str(raw.get("fast_model", defaults.fast_model)).strip(),
        deep_model=str(raw.get("deep_model", defaults.deep_model)).strip(),
        complexity_threshold=float(
            raw.get("complexity_threshold", defaults.complexity_threshold)
        ),
        escalate_below_confidence=float(
            raw.get("escalate_below_confidence", defaults.escalate_below_confidence)
        ),
        fast_max_chunks=int(raw.get("fast_max_chunks", defaults.fast_max_chunks)),
    )


__all__ = [
    "LLMConfig",
    "RoutingConfig",
    "load_llm_config",
    "DEFAULT_LLM_CONFIG_PATH",
    "DEFAULT_PROVIDER",
    "DEFAULT_MODELS",
    "DEFAULT_MAX_INPUT_CHARS",
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_MAX_CHUNKS",
]
