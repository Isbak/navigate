"""The token-usage record a provider reports for a single call."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    """Token usage from one LLM completion.

    ``cache_read_tokens`` / ``cache_write_tokens`` carry Anthropic's
    ``cache_read_input_tokens`` / ``cache_creation_input_tokens`` (0 for
    providers that do not report them). They are tracked separately - and never
    folded into ``input_tokens`` - so a later prompt-caching optimization is
    priced correctly without a schema change. ``latency_ms`` is the wall-clock
    time of the provider call, an input to "where is the spend going" analysis.
    """

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


__all__ = ["Usage"]
