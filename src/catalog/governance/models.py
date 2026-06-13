"""Controlled vocabularies for the knowledge governance layer.

These enums name the lifecycle every governed object moves through. They are
deliberately separate from the consolidation layer's ``ReviewState`` (PROPOSED /
REVIEWED / APPROVED / REJECTED), which governs whether an object is *exported*:
governance adds the operational states an organization actually manages -
freshness (is it current?) and a richer review workflow that includes
"needs attention" and "archived".
"""

from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class FreshnessState(_StrEnum):
    """How current an object's supporting evidence is."""

    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"
    ARCHIVED = "ARCHIVED"


class ReviewWorkflowState(_StrEnum):
    """The governance review workflow (distinct from export review status)."""

    PENDING_REVIEW = "PENDING_REVIEW"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


# States that still require a human's eyes - the review queue.
OPEN_REVIEW_STATES = (
    ReviewWorkflowState.PENDING_REVIEW.value,
    ReviewWorkflowState.NEEDS_ATTENTION.value,
)


class OwnerType(_StrEnum):
    """Who can own a piece of knowledge."""

    TEAM = "Team"
    PERSON = "Person"
    DOMAIN = "Domain"


class AlertSeverity(_StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertType(_StrEnum):
    """Every governance condition that warrants an operator's attention."""

    STALE_KNOWLEDGE = "stale_knowledge"
    STALE_REVIEW = "stale_review"
    ORPHANED_OBJECT = "orphaned_object"
    MISSING_OWNER = "missing_owner"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    DUPLICATE_OBJECT = "duplicate_object"
    DUPLICATE_RELATIONSHIP = "duplicate_relationship"
    QUALITY_DEGRADATION = "quality_degradation"
    KNOWLEDGE_DRIFT = "knowledge_drift"


class ChangeType(_StrEnum):
    """Entries in the change log / audit trail."""

    OBJECT_ADDED = "object_added"
    OBJECT_REMOVED = "object_removed"
    RELATIONSHIP_ADDED = "relationship_added"
    RELATIONSHIP_REMOVED = "relationship_removed"
    CONFIDENCE_CHANGED = "confidence_changed"
    OWNERSHIP_CHANGED = "ownership_changed"
    FRESHNESS_CHANGED = "freshness_changed"
    REVIEW_CHANGED = "review_changed"
    DRIFT_DETECTED = "drift_detected"


__all__ = [
    "FreshnessState",
    "ReviewWorkflowState",
    "OPEN_REVIEW_STATES",
    "OwnerType",
    "AlertSeverity",
    "AlertType",
    "ChangeType",
]
