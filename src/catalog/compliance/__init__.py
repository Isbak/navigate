"""Compliance & standards layer.

Turns the ``Standard`` / ``Requirement`` knowledge objects mined from standards
into an auditable compliance posture: it maps the organization's controls onto
requirements, derives an evidence-backed assessment status for each, and answers
"are we compliant with X, and prove it?" / "where are our gaps?".

It builds entirely on the existing platform invariants - traceable evidence,
human approval, decline-don't-hallucinate - and never concludes compliance on
its own: the engine proposes, humans approve.
"""

from __future__ import annotations

from .config import ComplianceConfig, load_compliance_config
from .importer import ImportStats, import_standard, load_catalog, load_equations
from .models import (
    Assessment,
    AssessmentEvidence,
    AssessmentStatus,
    AssessStats,
    ComplianceReviewState,
    Equation,
    ObligationLevel,
    Requirement,
    Standard,
)
from .service import assess, coverage, gaps, prove, review_assessment
from .sync import sync_equations, sync_requirements

__all__ = [
    "ComplianceConfig",
    "load_compliance_config",
    "ImportStats",
    "import_standard",
    "load_catalog",
    "load_equations",
    "Assessment",
    "AssessmentEvidence",
    "AssessmentStatus",
    "AssessStats",
    "ComplianceReviewState",
    "Equation",
    "ObligationLevel",
    "Requirement",
    "Standard",
    "assess",
    "coverage",
    "gaps",
    "prove",
    "review_assessment",
    "sync_requirements",
    "sync_equations",
]
