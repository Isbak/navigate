"""Answer confidence - how much to trust a graph-backed answer.

The confidence the assistant reports is *not* the model's self-assessment (which
would defeat the point). It is computed from the retrieval itself, blending four
signals the prompt calls out:

* **object confidence**       - how strong the matched knowledge objects are
* **relationship confidence** - how strong the connecting relationships are
* **evidence confidence**     - how strong the supporting quotes are
* **coverage**                - did every named term resolve, and is there evidence

The blended score maps to a band - **High / Medium / Low** - which is what a user
sees next to the answer. An answer with no evidence is always Low (and the
assistant declines to answer it at all).
"""

from __future__ import annotations

from dataclasses import dataclass

from .retrieval import GraphRetrieval

HIGH_THRESHOLD = 0.75
MEDIUM_THRESHOLD = 0.50

# Relative weights of the four signals (normalised internally).
_WEIGHTS = {
    "object": 0.30,
    "relationship": 0.20,
    "evidence": 0.30,
    "coverage": 0.20,
}


@dataclass(frozen=True)
class ConfidenceComponents:
    """The four signals plus the blended score and band."""

    object_confidence: float
    relationship_confidence: float
    evidence_confidence: float
    coverage: float
    score: float
    band: str


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def confidence_band(score: float) -> str:
    if score >= HIGH_THRESHOLD:
        return "High"
    if score >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"


def _coverage(retrieval: GraphRetrieval) -> float:
    """Fraction of named terms resolved, discounted when evidence is absent."""

    resolved = len(retrieval.seeds)
    requested = resolved + len(retrieval.unresolved_terms)
    term_coverage = 1.0 if requested == 0 else resolved / requested
    if not retrieval.evidence:
        term_coverage *= 0.5
    return term_coverage


def score_confidence(retrieval: GraphRetrieval) -> ConfidenceComponents:
    """Blend the four retrieval signals into a score and a High/Medium/Low band."""

    # Object confidence is anchored on the seeds (the things actually asked
    # about); neighbours inform context but should not dilute the headline.
    seed_objects = [o for o in retrieval.objects if o.is_seed] or retrieval.objects
    object_conf = _mean([o.confidence for o in seed_objects])
    relationship_conf = _mean([r.confidence for r in retrieval.relationships])
    evidence_conf = _mean([e.confidence for e in retrieval.evidence])
    coverage = _coverage(retrieval)

    # When there are no relationships, redistribute that weight onto evidence so
    # a well-evidenced single-object lookup is not unfairly penalised.
    weights = dict(_WEIGHTS)
    if not retrieval.relationships:
        weights["evidence"] += weights.pop("relationship")
        relationship_conf = 0.0

    total_weight = sum(weights.values())
    score = (
        weights.get("object", 0.0) * object_conf
        + weights.get("relationship", 0.0) * relationship_conf
        + weights.get("evidence", 0.0) * evidence_conf
        + weights.get("coverage", 0.0) * coverage
    ) / total_weight

    score = round(max(0.0, min(1.0, score)), 3)
    return ConfidenceComponents(
        object_confidence=round(object_conf, 3),
        relationship_confidence=round(relationship_conf, 3),
        evidence_confidence=round(evidence_conf, 3),
        coverage=round(coverage, 3),
        score=score,
        band=confidence_band(score),
    )


__all__ = [
    "HIGH_THRESHOLD",
    "MEDIUM_THRESHOLD",
    "ConfidenceComponents",
    "confidence_band",
    "score_confidence",
]
