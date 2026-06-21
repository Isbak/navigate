"""Quality scoring for governed knowledge objects.

A quality score (0-100) is the single number that answers "how much should I
trust this object?" It blends the six factors the spec names:

* **evidence**     - how many supporting evidence rows it has (saturating).
* **review**       - where it sits in the review workflow (approved is best).
* **freshness**    - the lifecycle freshness score (current beats stale).
* **consistency**  - the fraction of its relationships not rejected on review.
* **owner**        - whether someone owns it (accountability).
* **confidence**   - the consolidation confidence carried from the graph.

Each factor is normalized to ``[0, 1]``; the weighted blend is scaled to 0-100.
The function is pure and deterministic, so a given object always scores the same
and the factors can be tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import QualityConfig
from .models import ReviewWorkflowState

# How each review-workflow state contributes to the review factor.
_REVIEW_WEIGHT = {
    ReviewWorkflowState.APPROVED.value: 1.0,
    ReviewWorkflowState.NEEDS_ATTENTION.value: 0.5,
    ReviewWorkflowState.PENDING_REVIEW.value: 0.4,
    ReviewWorkflowState.ARCHIVED.value: 0.2,
    ReviewWorkflowState.REJECTED.value: 0.0,
}


@dataclass(frozen=True)
class QualityInputs:
    evidence_count: int = 0
    review_state: str = ReviewWorkflowState.PENDING_REVIEW.value
    freshness_score: float = 1.0
    relationship_total: int = 0
    relationship_rejected: int = 0
    has_owner: bool = False
    confidence: float = 0.0


def _saturate(value: int, target: int) -> float:
    if target <= 0:
        return 1.0
    return max(0.0, min(1.0, value / target))


def score_quality(
    inputs: QualityInputs, config: QualityConfig | None = None
) -> dict[str, float]:
    """Return the quality score and its component factors.

    The result dict carries the six factor scores (each in ``[0, 1]``) and a
    ``quality_score`` in ``[0, 100]`` so callers can both store the headline
    number and explain how it was reached.
    """

    config = config or QualityConfig()

    evidence = _saturate(inputs.evidence_count, config.target_evidence)
    review = _REVIEW_WEIGHT.get(inputs.review_state, 0.4)
    freshness = max(0.0, min(1.0, inputs.freshness_score))
    if inputs.relationship_total > 0:
        consistency = 1.0 - inputs.relationship_rejected / inputs.relationship_total
    else:
        consistency = 1.0
    consistency = max(0.0, min(1.0, consistency))
    owner = 1.0 if inputs.has_owner else 0.0
    confidence = max(0.0, min(1.0, inputs.confidence))

    weights = (
        config.weight_evidence,
        config.weight_review,
        config.weight_freshness,
        config.weight_consistency,
        config.weight_owner,
        config.weight_confidence,
    )
    factors = (evidence, review, freshness, consistency, owner, confidence)
    total_weight = sum(weights) or 1.0
    blended = sum(w * f for w, f in zip(weights, factors, strict=False)) / total_weight

    return {
        "evidence_score": round(evidence, 3),
        "review_score": round(review, 3),
        "freshness_score": round(freshness, 3),
        "consistency_score": round(consistency, 3),
        "owner_score": round(owner, 3),
        "confidence_score": round(confidence, 3),
        "quality_score": round(blended * 100, 1),
    }


__all__ = ["QualityInputs", "score_quality"]
