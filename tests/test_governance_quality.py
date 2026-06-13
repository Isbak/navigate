"""Tests for governance quality scoring (Prompt #10)."""

from catalog.governance.config import QualityConfig
from catalog.governance.models import ReviewWorkflowState
from catalog.governance.quality import QualityInputs, score_quality


def test_quality_in_range():
    result = score_quality(QualityInputs())
    assert 0.0 <= result["quality_score"] <= 100.0


def test_well_supported_owned_approved_scores_high():
    strong = score_quality(
        QualityInputs(
            evidence_count=8,
            review_state=ReviewWorkflowState.APPROVED.value,
            freshness_score=1.0,
            relationship_total=4,
            relationship_rejected=0,
            has_owner=True,
            confidence=0.95,
        )
    )
    assert strong["quality_score"] >= 90.0


def test_weak_unowned_pending_scores_low():
    weak = score_quality(
        QualityInputs(
            evidence_count=0,
            review_state=ReviewWorkflowState.PENDING_REVIEW.value,
            freshness_score=0.1,
            relationship_total=2,
            relationship_rejected=2,
            has_owner=False,
            confidence=0.1,
        )
    )
    assert weak["quality_score"] <= 25.0


def test_owner_assignment_raises_quality():
    base = QualityInputs(evidence_count=3, confidence=0.7)
    without = score_quality(base)
    with_owner = score_quality(
        QualityInputs(evidence_count=3, confidence=0.7, has_owner=True)
    )
    assert with_owner["quality_score"] > without["quality_score"]
    assert with_owner["owner_score"] == 1.0
    assert without["owner_score"] == 0.0


def test_rejected_relationships_lower_consistency():
    consistent = score_quality(
        QualityInputs(relationship_total=4, relationship_rejected=0)
    )
    inconsistent = score_quality(
        QualityInputs(relationship_total=4, relationship_rejected=3)
    )
    assert consistent["consistency_score"] == 1.0
    assert inconsistent["consistency_score"] < 1.0
    assert consistent["quality_score"] > inconsistent["quality_score"]


def test_no_relationships_is_neutral_not_penalized():
    result = score_quality(QualityInputs(relationship_total=0))
    assert result["consistency_score"] == 1.0


def test_evidence_saturates_at_target():
    config = QualityConfig(target_evidence=5)
    at_target = score_quality(QualityInputs(evidence_count=5), config)
    beyond = score_quality(QualityInputs(evidence_count=50), config)
    assert at_target["evidence_score"] == 1.0
    assert beyond["evidence_score"] == 1.0


def test_release_governance_outranks_launchpad_example():
    # The spec's worked example: Release Governance (92) outranks Launchpad (71).
    release_governance = score_quality(
        QualityInputs(
            evidence_count=8,
            review_state=ReviewWorkflowState.APPROVED.value,
            freshness_score=1.0,
            relationship_total=5,
            relationship_rejected=0,
            has_owner=True,
            confidence=0.92,
        )
    )
    launchpad = score_quality(
        QualityInputs(
            evidence_count=3,
            review_state=ReviewWorkflowState.PENDING_REVIEW.value,
            freshness_score=0.7,
            relationship_total=3,
            relationship_rejected=1,
            has_owner=False,
            confidence=0.71,
        )
    )
    assert release_governance["quality_score"] > launchpad["quality_score"]
