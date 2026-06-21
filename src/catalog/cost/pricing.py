"""Model pricing: turn token usage into USD.

Rates live in ``config/pricing.yml`` (USD per 1,000,000 tokens) so they can be
edited without touching code - they change far more often than provider wiring,
which is why they are kept out of ``llm.yml``. The loader is tolerant: a missing
file yields an empty table, so every cost is ``None`` (tokens are still tracked)
rather than an error. A model with no configured rate is "unpriced" the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .usage import Usage

DEFAULT_PRICING_PATH = Path("config/pricing.yml")


@dataclass(frozen=True)
class ModelRate:
    """Per-million-token USD rates for one model.

    ``cache_read_per_1m`` / ``cache_write_per_1m`` are optional; when unset they
    fall back to the input rate so cached tokens are never billed for free or
    dropped.
    """

    input_per_1m: float
    output_per_1m: float
    cache_read_per_1m: float | None = None
    cache_write_per_1m: float | None = None


@dataclass(frozen=True)
class PricingTable:
    rates: dict[str, ModelRate]
    currency: str = "USD"

    def rate_for(self, model: str) -> ModelRate | None:
        return self.rates.get(model)


def _as_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_pricing(path: str | Path = DEFAULT_PRICING_PATH) -> PricingTable:
    """Load the pricing table, tolerating a missing or malformed file."""

    config_path = Path(path)
    if not config_path.exists():
        return PricingTable(rates={})

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    currency = str(raw.get("currency", "USD"))
    rates: dict[str, ModelRate] = {}
    for model, spec in (raw.get("models") or {}).items():
        if not isinstance(spec, dict):
            continue
        input_rate = _as_float(spec.get("input"))
        output_rate = _as_float(spec.get("output"))
        if input_rate is None or output_rate is None:
            continue
        rates[str(model)] = ModelRate(
            input_per_1m=input_rate,
            output_per_1m=output_rate,
            cache_read_per_1m=_as_float(spec.get("cache_read")),
            cache_write_per_1m=_as_float(spec.get("cache_write")),
        )
    return PricingTable(rates=rates, currency=currency)


def compute_cost(usage: Usage, pricing: PricingTable) -> float | None:
    """USD cost of ``usage``, or ``None`` when the model has no configured rate.

    Cached tokens are billed separately from ``input_tokens`` (Anthropic reports
    them as distinct counts, so summing does not double-count) at their own rate
    when set, otherwise at the input rate.
    """

    rate = pricing.rate_for(usage.model)
    if rate is None:
        return None
    cache_read_rate = (
        rate.cache_read_per_1m if rate.cache_read_per_1m is not None else rate.input_per_1m
    )
    cache_write_rate = (
        rate.cache_write_per_1m if rate.cache_write_per_1m is not None else rate.input_per_1m
    )
    return (
        usage.input_tokens / 1_000_000 * rate.input_per_1m
        + usage.output_tokens / 1_000_000 * rate.output_per_1m
        + usage.cache_read_tokens / 1_000_000 * cache_read_rate
        + usage.cache_write_tokens / 1_000_000 * cache_write_rate
    )


__all__ = [
    "ModelRate",
    "PricingTable",
    "load_pricing",
    "compute_cost",
    "DEFAULT_PRICING_PATH",
]
