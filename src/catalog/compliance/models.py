"""Dataclasses and controlled vocabularies for the compliance layer.

The compliance layer maps the organization's controls (the ``Capability`` /
``Process`` / ``Platform`` knowledge objects already consolidated from internal
documents) onto the ``Requirement`` objects mined from standards, and records a
human-curated *assessment* of whether each requirement is met - with traceable
evidence, exactly like the rest of the platform.

Nothing here concludes compliance on its own: the engine *derives* a status from
the evidence, but a claim is only trusted once a human APPROVES the assessment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AssessmentStatus(str, Enum):
    """How well a control is judged to meet a requirement."""

    SATISFIED = "SATISFIED"
    PARTIAL = "PARTIAL"
    GAP = "GAP"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNASSESSED = "UNASSESSED"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class ObligationLevel(str, Enum):
    """The strength of a requirement's obligation."""

    MANDATORY = "MANDATORY"
    RECOMMENDED = "RECOMMENDED"
    OPTIONAL = "OPTIONAL"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class ComplianceReviewState(str, Enum):
    """Review workflow for a compliance assessment.

    An assessment is PROPOSED by the engine and only trusted once a human
    APPROVES it; REJECTED records an explicit "this claim is wrong".
    """

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


# Statuses that assert a control does (at least partly) meet a requirement and
# therefore must be backed by at least one evidence row.
EVIDENCED_STATUSES = (AssessmentStatus.SATISFIED.value, AssessmentStatus.PARTIAL.value)


@dataclass(frozen=True)
class Standard:
    """Enriched metadata for a ``Standard`` knowledge object."""

    object_id: str
    name: str
    authority: str = ""
    version: str = ""
    jurisdiction: str = ""
    effective_from: str = ""
    source_url: str = ""


@dataclass(frozen=True)
class Requirement:
    """Enriched metadata for a ``Requirement`` knowledge object."""

    object_id: str
    standard_object_id: str = ""
    clause_ref: str = ""
    title: str = ""
    requirement_text: str = ""
    obligation_level: str = ObligationLevel.MANDATORY.value
    assessed_against_version: str = ""


@dataclass(frozen=True)
class Equation:
    """Enriched metadata for an ``Equation`` knowledge object.

    Carries the machine-readable payload the generic object model cannot hold:
    the formula ``expression``, the generated ``python_code`` and ``ast_json``
    (produced without ever executing the formula), the ``variables`` it reads,
    and whether it passed allowlist ``valid``ation.
    """

    object_id: str
    standard_object_id: str = ""
    requirement_object_id: str = ""
    clause_ref: str = ""
    symbol: str = ""
    title: str = ""
    expression: str = ""
    python_code: str = ""
    ast_json: str = ""
    variables: list[dict] = field(default_factory=list)
    latex: str = ""
    valid: bool = False
    validation_note: str = ""
    assessed_against_version: str = ""


@dataclass(frozen=True)
class AssessmentEvidence:
    """One quote backing an assessment."""

    artifact_id: str
    quote: str
    clause_ref: str = ""
    page_number: int | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class Assessment:
    """A control-vs-requirement compliance judgement."""

    requirement_object_id: str
    control_object_id: str | None
    status: str
    assessed_against_version: str = ""
    rationale: str = ""
    assessor: str = "engine"
    evidence: list[AssessmentEvidence] = field(default_factory=list)


@dataclass
class AssessStats:
    """Aggregate counters for one ``assess`` run."""

    requirements_assessed: int = 0
    satisfied: int = 0
    partial: int = 0
    gaps: int = 0
    not_applicable: int = 0
    coverage: float = 0.0
    errors: int = 0

    def as_dict(self) -> dict:
        return {
            "requirements_assessed": self.requirements_assessed,
            "satisfied": self.satisfied,
            "partial": self.partial,
            "gaps": self.gaps,
            "not_applicable": self.not_applicable,
            "coverage": self.coverage,
            "errors": self.errors,
        }


__all__ = [
    "AssessmentStatus",
    "ObligationLevel",
    "ComplianceReviewState",
    "EVIDENCED_STATUSES",
    "Standard",
    "Requirement",
    "Equation",
    "AssessmentEvidence",
    "Assessment",
    "AssessStats",
]
