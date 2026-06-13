"""Confidence scoring for knowledge objects.

A consolidated object's confidence answers "how much should I trust that this is
a real, well-supported thing?" It blends five signals named in the spec:

* **LLM confidence** - the average confidence the semantic layer assigned to the
  mentions that formed this object.
* **document support** - how many *distinct* documents mention it (breadth beats
  repetition: 27 documents is strong evidence).
* **mention support** - how many times it was mentioned overall.
* **relationship consistency** - the fraction of its relationships that have not
  been rejected (contradicted) on review.
* **review history** - human approval nudges confidence up; rejection drives it
  down hard.

The weights sum to 1.0 so the pre-review score is itself a probability in
``[0, 1]``; review adjustments are applied afterward and the result is clamped.
The function is pure and deterministic, which keeps it easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ReviewState


@dataclass(frozen=True)
class ScoringConfig:
    weight_llm: float = 0.45
    weight_docs: float = 0.25
    weight_mentions: float = 0.15
    weight_consistency: float = 0.15
    # Document / mention counts at which support saturates to 1.0.
    target_documents: int = 8
    target_mentions: int = 12
    approved_bonus: float = 0.05
    rejected_penalty: float = 0.6


@dataclass(frozen=True)
class ScoringInputs:
    mention_confidences: list[float]
    document_count: int
    mention_count: int
    relationship_total: int = 0
    relationship_rejected: int = 0
    review_actions: tuple[str, ...] = ()


def _saturate(value: int, target: int) -> float:
    if target <= 0:
        return 1.0
    return max(0.0, min(1.0, value / target))


def score_object(inputs: ScoringInputs, config: ScoringConfig | None = None) -> float:
    """Return a confidence in ``[0.0, 1.0]`` for one knowledge object."""

    config = config or ScoringConfig()

    confidences = [c for c in inputs.mention_confidences if c is not None]
    llm_conf = sum(confidences) / len(confidences) if confidences else 0.0

    doc_support = _saturate(inputs.document_count, config.target_documents)
    mention_support = _saturate(inputs.mention_count, config.target_mentions)

    if inputs.relationship_total > 0:
        consistency = 1.0 - (inputs.relationship_rejected / inputs.relationship_total)
    else:
        consistency = 1.0  # no relationships is neutral, not penalized

    base = (
        config.weight_llm * llm_conf
        + config.weight_docs * doc_support
        + config.weight_mentions * mention_support
        + config.weight_consistency * consistency
    )

    actions = set(inputs.review_actions)
    if ReviewState.REJECTED.value in actions:
        base *= 1.0 - config.rejected_penalty
    elif ReviewState.APPROVED.value in actions:
        base += config.approved_bonus

    return round(max(0.0, min(1.0, base)), 3)


__all__ = ["ScoringConfig", "ScoringInputs", "score_object"]
