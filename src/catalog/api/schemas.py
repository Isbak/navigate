"""Pydantic response/request models for the REST API.

These are the public contract navigate-compass (or any client) consumes. They
are deliberately kept separate from the SQLite/database row shapes: the database
is an internal, regenerable index, while these schemas are a stable surface. The
serializers in :mod:`catalog.api.serializers` map rows onto these models.
"""

from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Uniform envelope returned by every list endpoint."""

    items: list[T]
    limit: int
    offset: int
    total: int


class ErrorResponse(BaseModel):
    """The consistent error shape returned for every failure."""

    error: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# -- core ---------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    database: dict[str, Any]
    version: str


class StatsResponse(BaseModel):
    artifact_count: int
    link_count: int
    knowledge_object_count: int
    relationship_count: int
    evidence_count: int
    pending_review_count: int
    stale_object_count: int
    last_scan: Optional[dict[str, Any]] = None


# -- artifacts ----------------------------------------------------------------

class Artifact(BaseModel):
    id: str
    path: str
    filename: str
    file_type: str
    size_bytes: Optional[int] = None
    created_at: Optional[str] = None
    modified_at: Optional[str] = None
    sha256: Optional[str] = None
    source_system: Optional[str] = None
    scan_status: Optional[str] = None
    first_seen_at: Optional[str] = None
    last_scanned_at: Optional[str] = None
    extraction_status: str = "PENDING"
    classification_status: str = "UNCLASSIFIED"


# -- links --------------------------------------------------------------------

class Link(BaseModel):
    id: int
    source_artifact_id: str
    raw_url: str
    normalized_url: str
    anchor_text: Optional[str] = None
    target_system: Optional[str] = None
    target_type: Optional[str] = None
    link_kind: Optional[str] = None
    discovered_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    status: Optional[str] = None


class CountItem(BaseModel):
    key: Optional[str] = None
    count: int


class LinkStats(BaseModel):
    total: int
    by_target_system: list[CountItem]
    by_target_type: list[CountItem]
    by_link_kind: list[CountItem]


class TopTarget(BaseModel):
    url: str
    count: int


# -- knowledge ----------------------------------------------------------------

class KnowledgeObject(BaseModel):
    id: str
    name: str
    object_type: str
    description: Optional[str] = None
    canonical_name: Optional[str] = None
    confidence: Optional[float] = None
    status: Optional[str] = None
    merge_confidence: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    review_status: Optional[str] = None
    freshness_state: Optional[str] = None
    quality_score: Optional[float] = None
    owner: Optional[str] = None
    # Per-row child counts, so a list/table view can show badges without an
    # N+1 fan-out to the relationships / evidence / mentions sub-resources.
    relationship_count: Optional[int] = None
    evidence_count: Optional[int] = None
    mention_count: Optional[int] = None


class Relationship(BaseModel):
    id: int
    source_object: str
    predicate: str
    target_object: str
    confidence: Optional[float] = None
    evidence: Optional[Any] = None
    review_status: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class Mention(BaseModel):
    id: int
    knowledge_object_id: str
    artifact_id: str
    confidence: Optional[float] = None
    source_text: Optional[str] = None
    created_at: Optional[str] = None


class Evidence(BaseModel):
    id: int
    knowledge_object_id: str
    artifact_id: str
    quote: Optional[str] = None
    page_number: Optional[int] = None
    slide_number: Optional[int] = None
    confidence: Optional[float] = None
    created_at: Optional[str] = None


# -- governance ---------------------------------------------------------------

class GovernanceAlert(BaseModel):
    id: int
    alert_type: str
    severity: str
    object_id: Optional[str] = None
    message: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None


class ReviewQueueItem(BaseModel):
    object_id: str
    name: Optional[str] = None
    object_type: Optional[str] = None
    review_state: Optional[str] = None
    freshness_state: Optional[str] = None
    last_confidence: Optional[float] = None


class StaleItem(BaseModel):
    object_id: str
    name: Optional[str] = None
    object_type: Optional[str] = None
    freshness_state: Optional[str] = None
    freshness_score: Optional[float] = None
    last_seen_at: Optional[str] = None


class QualityItem(BaseModel):
    object_id: str
    canonical_name: Optional[str] = None
    object_type: Optional[str] = None
    quality_score: Optional[float] = None
    evidence_count: Optional[int] = None
    document_count: Optional[int] = None


class QualityResponse(BaseModel):
    average_quality: float
    items: list[QualityItem]


class DomainHealth(BaseModel):
    """A knowledge domain (business area) and its governance health."""

    domain: str
    owner: Optional[str] = None
    object_count: int
    avg_quality: float
    avg_freshness: float
    review_backlog: int


class ChangeLogEntry(BaseModel):
    """One entry from the governance change-log (audit) feed."""

    id: int
    change_type: str
    target_kind: Optional[str] = None
    object_id: Optional[str] = None
    field: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    detail: Optional[str] = None
    detected_at: Optional[str] = None


class GrowthPoint(BaseModel):
    """Counts for a single period of the knowledge-growth trend.

    ``*_added`` is new in the period; ``*_total`` is the cumulative count up to
    and including the period.
    """

    period: str
    artifacts_added: int
    artifacts_total: int
    objects_added: int
    objects_total: int
    relationships_added: int
    relationships_total: int


class GrowthTrend(BaseModel):
    interval: str
    points: list[GrowthPoint]


# -- graph --------------------------------------------------------------------

class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    confidence: Optional[float] = None
    status: Optional[str] = None
    documents: Optional[int] = None
    mentions: Optional[int] = None


class GraphEdge(BaseModel):
    id: int
    source: str
    target: str
    predicate: str
    confidence: Optional[float] = None
    status: Optional[str] = None


class GraphNeighbor(BaseModel):
    id: str
    label: str
    type: str
    direction: str


class NeighborsResponse(BaseModel):
    object_id: str
    neighbors: dict[str, list[GraphNeighbor]]


class ImpactItem(BaseModel):
    id: str
    label: str


class ImpactResponse(BaseModel):
    object_id: str
    impact: dict[str, list[ImpactItem]]


class PathHop(BaseModel):
    from_: str = Field(alias="from")
    to: str
    predicate: str
    forward: bool

    model_config = {"populate_by_name": True}


class PathResponse(BaseModel):
    source: str
    target: str
    found: bool
    hops: list[PathHop]


class GraphExport(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# -- jobs ---------------------------------------------------------------------

class Job(BaseModel):
    id: int
    job_type: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    result_summary: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None


# -- ask / GraphRAG -----------------------------------------------------------

class AskRequest(BaseModel):
    question: str
    depth: int = 2
    show_context: bool = False
    show_evidence: bool = True


class AskResponse(BaseModel):
    answer: str
    confidence: str
    objects_used: list[dict[str, Any]] = Field(default_factory=list)
    relationships_used: list[dict[str, Any]] = Field(default_factory=list)
    evidence_used: list[dict[str, Any]] = Field(default_factory=list)
    context: Optional[str] = None


# -- action acknowledgements --------------------------------------------------

class ActionResponse(BaseModel):
    id: str
    status: str
    message: str


class ConfidenceApprovalRequest(BaseModel):
    min_confidence: float = Field(ge=0.0, le=1.0)
    max_confidence: float = Field(ge=0.0, le=1.0)
    include_reviewed: bool = False
    note: str = ""


class ConfidenceApprovalResponse(BaseModel):
    min_confidence: float
    max_confidence: float
    objects_approved: int = 0
    relationships_approved: int = 0
    message: str


# -- compliance ---------------------------------------------------------------

class ComplianceStandard(BaseModel):
    object_id: str
    name: str
    authority: str = ""
    version: str = ""
    jurisdiction: str = ""
    status: Optional[str] = None


class ComplianceRequirement(BaseModel):
    object_id: str
    name: str
    standard_object_id: str = ""
    clause_ref: str = ""
    title: str = ""
    requirement_text: str = ""
    obligation_level: str = ""
    status: Optional[str] = None


class ComplianceEquationVariable(BaseModel):
    symbol: str
    description: str = ""
    unit: str = ""


class ComplianceEquation(BaseModel):
    object_id: str
    name: str
    standard_object_id: str = ""
    requirement_object_id: str = ""
    clause_ref: str = ""
    symbol: str = ""
    title: str = ""
    expression: str = ""
    python_code: str = ""
    ast_json: str = ""
    variables: list[ComplianceEquationVariable] = []
    latex: str = ""
    valid: bool = False
    validation_note: str = ""
    status: Optional[str] = None


class ComplianceCoverageStandard(BaseModel):
    standard_object_id: str
    standard_name: str
    total: int
    satisfied: int
    partial: int
    coverage: float


class ComplianceCoverageResponse(BaseModel):
    overall: float
    standards: list[ComplianceCoverageStandard]


class ComplianceGap(BaseModel):
    object_id: str
    requirement_name: str
    clause_ref: str = ""
    title: str = ""
    obligation_level: str = ""
    standard_object_id: str = ""
    standard_name: str = ""


class ComplianceAssessment(BaseModel):
    id: int
    requirement_object_id: str
    requirement_name: Optional[str] = None
    control_object_id: Optional[str] = None
    control_name: Optional[str] = None
    status: str
    review_status: str
    assessed_against_version: str = ""
    rationale: str = ""


class ComplianceEvidence(BaseModel):
    artifact_id: Optional[str] = None
    quote: str = ""
    clause_ref: str = ""
    page_number: Optional[int] = None
    confidence: Optional[float] = None


class ComplianceProofAssessment(BaseModel):
    assessment_id: int
    control_object_id: Optional[str] = None
    control_name: Optional[str] = None
    status: str
    rationale: str = ""
    assessed_against_version: str = ""
    evidence: list[ComplianceEvidence] = Field(default_factory=list)


class ComplianceProofResponse(BaseModel):
    found: bool
    proven: bool
    term: str
    message: str = ""
    requirement: dict[str, Any] = Field(default_factory=dict)
    assessments: list[ComplianceProofAssessment] = Field(default_factory=list)


__all__ = [
    "PaginatedResponse",
    "ErrorResponse",
    "HealthResponse",
    "StatsResponse",
    "Artifact",
    "Link",
    "CountItem",
    "LinkStats",
    "TopTarget",
    "KnowledgeObject",
    "Relationship",
    "Mention",
    "Evidence",
    "GovernanceAlert",
    "ReviewQueueItem",
    "StaleItem",
    "QualityItem",
    "QualityResponse",
    "DomainHealth",
    "ChangeLogEntry",
    "GrowthPoint",
    "GrowthTrend",
    "GraphNode",
    "GraphEdge",
    "GraphNeighbor",
    "NeighborsResponse",
    "ImpactItem",
    "ImpactResponse",
    "PathHop",
    "PathResponse",
    "GraphExport",
    "Job",
    "AskRequest",
    "AskResponse",
    "ActionResponse",
    "ConfidenceApprovalRequest",
    "ConfidenceApprovalResponse",
    "ComplianceStandard",
    "ComplianceRequirement",
    "ComplianceCoverageStandard",
    "ComplianceCoverageResponse",
    "ComplianceGap",
    "ComplianceAssessment",
    "ComplianceEvidence",
    "ComplianceProofAssessment",
    "ComplianceProofResponse",
]
