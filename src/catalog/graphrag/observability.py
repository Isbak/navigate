"""Observability - a structured trace for every question answered.

The prompt requires logging the question, how much was retrieved, the prompt
size, and the response time. A :class:`Trace` captures exactly that, and
:func:`log_trace` emits it at INFO through the standard logging the rest of the
package already uses, so it shows up under ``catalog -v`` without any new
infrastructure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

LOGGER = logging.getLogger("catalog.graphrag")


@dataclass(frozen=True)
class Trace:
    """One answered question's observable footprint."""

    question: str
    reasoning_type: str
    objects_retrieved: int
    relationships_retrieved: int
    evidence_count: int
    prompt_size: int
    response_time_ms: float
    confidence_band: str = ""

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "reasoning_type": self.reasoning_type,
            "objects_retrieved": self.objects_retrieved,
            "relationships_retrieved": self.relationships_retrieved,
            "evidence_count": self.evidence_count,
            "prompt_size": self.prompt_size,
            "response_time_ms": round(self.response_time_ms, 1),
            "confidence_band": self.confidence_band,
        }


def log_trace(trace: Trace, logger: logging.Logger | None = None) -> None:
    (logger or LOGGER).info(
        "graphrag answered: type=%s objects=%d relationships=%d evidence=%d "
        "prompt_size=%d time_ms=%.1f confidence=%s question=%r",
        trace.reasoning_type,
        trace.objects_retrieved,
        trace.relationships_retrieved,
        trace.evidence_count,
        trace.prompt_size,
        trace.response_time_ms,
        trace.confidence_band,
        trace.question,
    )


__all__ = ["Trace", "log_trace", "LOGGER"]
