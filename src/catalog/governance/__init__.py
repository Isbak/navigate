"""Knowledge governance and continuous knowledge operations (Prompt #10).

This package turns the consolidated knowledge graph into a *governed* knowledge
platform. Where the earlier layers answered "what does the organization know?",
governance answers the trust questions:

* Which knowledge is trusted?      -> quality scoring + review workflow
* Which knowledge is stale?        -> the freshness lifecycle
* Who owns a capability?           -> ownership
* What changed recently?           -> the change log + evolution history
* What needs review?               -> the review queue + alerts
* Why should I trust this answer?  -> evidence, owner, reviewer, and audit trail

Nothing here adds retrieval, GraphRAG, vector search, or agents. It is pure
governance over the SQLite system of record: lifecycle, ownership, quality,
drift, alerts, and a full audit trail, all fully regenerable from a scan.
"""

from .models import (
    AlertSeverity,
    AlertType,
    ChangeType,
    FreshnessState,
    OwnerType,
    ReviewWorkflowState,
)
from .service import (
    GovernanceScanStats,
    approve_object,
    archive_object,
    flag_object,
    reject_object,
    run_scan,
)

__all__ = [
    "AlertSeverity",
    "AlertType",
    "ChangeType",
    "FreshnessState",
    "OwnerType",
    "ReviewWorkflowState",
    "GovernanceScanStats",
    "run_scan",
    "approve_object",
    "archive_object",
    "reject_object",
    "flag_object",
]
