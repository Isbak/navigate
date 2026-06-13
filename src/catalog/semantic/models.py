"""Dataclasses and controlled vocabularies for semantic classification.

These types describe what the LLM *proposes* for a single document. Nothing
here is a fact: every object carries a confidence in ``[0.0, 1.0]`` and, where
applicable, the supporting text it was derived from. The service layer stamps
each object with its provenance (artifact id, model, timestamp) and a
``review_status`` of NEW before persisting.

Storage tiers
-------------
``KnowledgeType`` separates what kind of claim an object represents. This phase
only ever emits OBSERVATION and HYPOTHESIS - never FACT.

* OBSERVATION - directly read off the document (its type, domains, the
  capabilities/entities it discusses).
* HYPOTHESIS  - an inferred claim (a decision the document seems to make, a risk
  it implies, a relationship between two things).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class KnowledgeType(str, Enum):
    OBSERVATION = "OBSERVATION"
    HYPOTHESIS = "HYPOTHESIS"
    FACT = "FACT"  # never produced in this phase; humans promote to FACT later

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class ReviewStatus(str, Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


# Controlled vocabularies. These guide the prompt and are the values we expect
# back; unknown values from the model are tolerated and normalized to "Other"
# (document_type) or kept verbatim (free-form names) by the parser.
DOCUMENT_TYPES = (
    "Governance",
    "Strategy",
    "Architecture",
    "Roadmap",
    "Project",
    "Meeting Notes",
    "Workshop",
    "Presentation",
    "Budget",
    "Report",
    "Requirements",
    "Technical Design",
    "Operating Model",
    "Training",
    "Other",
)

ENTITY_TYPES = (
    "Capability",
    "Initiative",
    "Team",
    "Product",
    "Platform",
    "Process",
    "Technology",
    "Concept",
    "Decision",
    "Risk",
)

RELATIONSHIP_PREDICATES = (
    "supports",
    "depends_on",
    "implements",
    "mentions",
    "references",
    "affects",
    "owned_by",
    "related_to",
)


@dataclass(frozen=True)
class DomainScore:
    """A domain the document touches, with the model's confidence."""

    domain: str
    confidence: float


@dataclass(frozen=True)
class CandidateEntity:
    entity_type: str
    name: str
    confidence: float
    supporting_text: str = ""


@dataclass(frozen=True)
class CandidateCapability:
    name: str
    confidence: float
    supporting_text: str = ""


@dataclass(frozen=True)
class CandidateDecision:
    decision_text: str
    confidence: float
    supporting_text: str = ""


@dataclass(frozen=True)
class CandidateRisk:
    risk_description: str
    confidence: float
    supporting_text: str = ""


@dataclass(frozen=True)
class CandidateRelationship:
    subject: str
    predicate: str
    object: str
    confidence: float
    supporting_text: str = ""


@dataclass(frozen=True)
class ClassificationResult:
    """Everything the LLM proposed for one document."""

    document_type: str
    type_confidence: float
    short_summary: str = ""
    long_summary: str = ""
    domains: list[DomainScore] = field(default_factory=list)
    entities: list[CandidateEntity] = field(default_factory=list)
    capabilities: list[CandidateCapability] = field(default_factory=list)
    decisions: list[CandidateDecision] = field(default_factory=list)
    risks: list[CandidateRisk] = field(default_factory=list)
    relationships: list[CandidateRelationship] = field(default_factory=list)


__all__ = [
    "KnowledgeType",
    "ReviewStatus",
    "DOCUMENT_TYPES",
    "ENTITY_TYPES",
    "RELATIONSHIP_PREDICATES",
    "DomainScore",
    "CandidateEntity",
    "CandidateCapability",
    "CandidateDecision",
    "CandidateRisk",
    "CandidateRelationship",
    "ClassificationResult",
]
