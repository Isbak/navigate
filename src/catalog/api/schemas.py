"""Pydantic response/request models for the REST API.

These are the public contract navigate-compass (or any client) consumes. They
are deliberately kept separate from the SQLite/database row shapes: the database
is an internal, regenerable index, while these schemas are a stable surface. The
serializers in :mod:`catalog.api.serializers` map rows onto these models.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

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
    last_scan: dict[str, Any] | None = None


# -- artifacts ----------------------------------------------------------------

class Artifact(BaseModel):
    id: str
    path: str
    filename: str
    file_type: str
    size_bytes: int | None = None
    created_at: str | None = None
    modified_at: str | None = None
    sha256: str | None = None
    source_system: str | None = None
    scan_status: str | None = None
    first_seen_at: str | None = None
    last_scanned_at: str | None = None
    extraction_status: str = "PENDING"
    classification_status: str = "UNCLASSIFIED"


# -- links --------------------------------------------------------------------

class Link(BaseModel):
    id: int
    source_artifact_id: str
    raw_url: str
    normalized_url: str
    anchor_text: str | None = None
    target_system: str | None = None
    target_type: str | None = None
    link_kind: str | None = None
    discovered_at: str | None = None
    last_seen_at: str | None = None
    status: str | None = None


class CountItem(BaseModel):
    key: str | None = None
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
    description: str | None = None
    canonical_name: str | None = None
    confidence: float | None = None
    status: str | None = None
    merge_confidence: float | None = None
    created_at: str | None = None
    updated_at: str | None = None
    review_status: str | None = None
    freshness_state: str | None = None
    quality_score: float | None = None
    owner: str | None = None
    # Per-row child counts, so a list/table view can show badges without an
    # N+1 fan-out to the relationships / evidence / mentions sub-resources.
    relationship_count: int | None = None
    evidence_count: int | None = None
    mention_count: int | None = None


class Relationship(BaseModel):
    id: int
    source_object: str
    predicate: str
    target_object: str
    confidence: float | None = None
    evidence: Any | None = None
    review_status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class Mention(BaseModel):
    id: int
    knowledge_object_id: str
    artifact_id: str
    confidence: float | None = None
    source_text: str | None = None
    created_at: str | None = None


class Evidence(BaseModel):
    id: int
    knowledge_object_id: str
    artifact_id: str
    quote: str | None = None
    page_number: int | None = None
    slide_number: int | None = None
    confidence: float | None = None
    created_at: str | None = None


# -- governance ---------------------------------------------------------------

class GovernanceAlert(BaseModel):
    id: int
    alert_type: str
    severity: str
    object_id: str | None = None
    message: str | None = None
    status: str | None = None
    created_at: str | None = None
    resolved_at: str | None = None


class ReviewQueueItem(BaseModel):
    object_id: str
    name: str | None = None
    object_type: str | None = None
    review_state: str | None = None
    freshness_state: str | None = None
    last_confidence: float | None = None


class StaleItem(BaseModel):
    object_id: str
    name: str | None = None
    object_type: str | None = None
    freshness_state: str | None = None
    freshness_score: float | None = None
    last_seen_at: str | None = None


class QualityItem(BaseModel):
    object_id: str
    canonical_name: str | None = None
    object_type: str | None = None
    quality_score: float | None = None
    evidence_count: int | None = None
    document_count: int | None = None


class QualityResponse(BaseModel):
    average_quality: float
    items: list[QualityItem]


class DomainHealth(BaseModel):
    """A knowledge domain (business area) and its governance health."""

    domain: str
    owner: str | None = None
    object_count: int
    avg_quality: float
    avg_freshness: float
    review_backlog: int


class ChangeLogEntry(BaseModel):
    """One entry from the governance change-log (audit) feed."""

    id: int
    change_type: str
    target_kind: str | None = None
    object_id: str | None = None
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    detail: str | None = None
    detected_at: str | None = None


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
    confidence: float | None = None
    status: str | None = None
    documents: int | None = None
    mentions: int | None = None


class GraphEdge(BaseModel):
    id: int
    source: str
    target: str
    predicate: str
    confidence: float | None = None
    status: str | None = None


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
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
    result_summary: dict[str, Any] | None = None
    created_at: str | None = None


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
    context: str | None = None


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
    status: str | None = None


class ComplianceRequirement(BaseModel):
    object_id: str
    name: str
    standard_object_id: str = ""
    clause_ref: str = ""
    title: str = ""
    requirement_text: str = ""
    obligation_level: str = ""
    status: str | None = None


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
    status: str | None = None


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
    requirement_name: str | None = None
    control_object_id: str | None = None
    control_name: str | None = None
    status: str
    review_status: str
    assessed_against_version: str = ""
    rationale: str = ""


class ComplianceEvidence(BaseModel):
    artifact_id: str | None = None
    quote: str = ""
    clause_ref: str = ""
    page_number: int | None = None
    confidence: float | None = None


class ComplianceProofAssessment(BaseModel):
    assessment_id: int
    control_object_id: str | None = None
    control_name: str | None = None
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

# -- cost / LLM usage ---------------------------------------------------------

class CostSummary(BaseModel):
    """Aggregate LLM token usage and spend across every recorded call."""

    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None = None
    unpriced_calls: int


class CostByOperation(BaseModel):
    operation: str | None = None
    calls: int
    total_tokens: int
    cost_usd: float | None = None


class CostByModel(BaseModel):
    model: str | None = None
    calls: int
    total_tokens: int
    cost_usd: float | None = None
    unpriced_calls: int


class CostPerDocument(BaseModel):
    artifact_id: str | None = None
    calls: int
    total_tokens: int
    cost_usd: float | None = None


class CostVsQuality(BaseModel):
    artifact_id: str | None = None
    document_type: str | None = None
    type_confidence: float | None = None
    calls: int
    total_tokens: int
    cost_usd: float | None = None


# -- graph analytics ----------------------------------------------------------

class GraphCentralNode(BaseModel):
    id: str
    label: str
    degree: int


class GraphDomain(BaseModel):
    """One object-type "domain" with its size and most-central concepts."""

    domain: str
    object_count: int
    relationship_count: int
    most_central: list[GraphCentralNode] = Field(default_factory=list)


# -- governance ownership / drift / history -----------------------------------

class OwnerAssignment(BaseModel):
    object_id: str
    owner_type: str
    owner_id: str
    assigned_at: str | None = None
    assigned_by: str | None = None


class AssignOwnerRequest(BaseModel):
    owner_type: str
    owner_id: str


class ObjectHistory(BaseModel):
    """Combined governance audit view for a single object."""

    object_id: str
    changes: list[ChangeLogEntry] = Field(default_factory=list)
    lifecycle: dict[str, Any] | None = None
    owner: OwnerAssignment | None = None


# -- agent review -------------------------------------------------------------

class AgentApproveRequest(BaseModel):
    """Override the configured agent-review policy for a single pass.

    Any field left ``None`` falls back to ``config/governance.yml``'s
    ``agent_review`` policy; the agent identity and thresholds are never taken
    from an in-loop model, only from config or an explicit human-issued request.
    """

    target: Literal["objects", "relationships", "all"] = "all"
    agent: str | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str = ""
    dry_run: bool = False


class AgentApproveResponse(BaseModel):
    reviewer: str
    dry_run: bool
    objects_approved: int
    relationships_approved: int
    objects_skipped: int
    relationships_skipped: int
    total_approved: int
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class RevertRequest(BaseModel):
    target_kind: Literal["object", "relationship"]
    target_id: str
    note: str = ""


class RevertResponse(BaseModel):
    reverted: bool
    target_kind: str
    target_id: str
    from_state: str = ""
    to_state: str = ""
    reason: str = ""


class RevertAgentRequest(BaseModel):
    agent: str | None = None
    since: str | None = None
    note: str = ""


class RevertAgentResponse(BaseModel):
    reverted: int
    skipped: int
    results: list[RevertResponse] = Field(default_factory=list)


# -- rdf ----------------------------------------------------------------------

class RdfStats(BaseModel):
    """Counts of what an RDF export would contain (approved data only)."""

    objects: int
    relationships: int
    evidence: int
    knowledge_triples: int
    relationship_triples: int
    provenance_triples: int


class RdfValidationFile(BaseModel):
    ok: bool
    triples: int
    error: str | None = None


class RdfValidation(BaseModel):
    files: dict[str, RdfValidationFile] = Field(default_factory=dict)


# -- ask / GraphRAG extensions ------------------------------------------------

class ExplainRequest(BaseModel):
    """Single-term GraphRAG request (explain / impact)."""

    term: str
    depth: int = 2
    show_context: bool = False
    show_evidence: bool = True


class CompareRequest(BaseModel):
    """Two-term GraphRAG request (compare / path-reason)."""

    term_a: str
    term_b: str
    depth: int = 2
    show_context: bool = False
    show_evidence: bool = True


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
    "CostSummary",
    "CostByOperation",
    "CostByModel",
    "CostPerDocument",
    "CostVsQuality",
    "GraphCentralNode",
    "GraphDomain",
    "OwnerAssignment",
    "AssignOwnerRequest",
    "ObjectHistory",
    "RdfStats",
    "RdfValidationFile",
    "RdfValidation",
    "ExplainRequest",
    "CompareRequest",
]
