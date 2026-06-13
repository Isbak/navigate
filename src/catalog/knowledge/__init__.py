"""Knowledge consolidation and graph foundation (Prompt #6).

This package converges the document-level proposals produced by the semantic
layer into reusable, cross-document *knowledge objects*. Three documents that
each talk about "Release Governance" / "Release Governance Model" / "Release
governance" collapse into a single ``capability_release_governance`` object with
traceable evidence and typed relationships to other objects.

What this is
------------
* A knowledge layer over documents: objects, mentions, evidence, relationships.
* Graph-ready: every object has a stable, URI-ready id (``platform_salesforce``)
  that a future RDF mapping can adopt without renaming anything.
* Human-in-the-loop: the resolver and the (optional) LLM *propose*; only APPROVED
  objects and relationships are trusted.

What this is NOT (explicitly out of scope this phase)
-----------------------------------------------------
No RDF, no Jena, no SPARQL, no GraphRAG, and no vector-search UI. The graph
export produces JSON for a *future* visualization, not a UI.
"""

from __future__ import annotations

from .analytics import (
    conflicting_evidence,
    duplicate_candidates,
    most_connected,
    most_mentioned,
    top_by_type,
)
from .export import build_edges, build_nodes, export_graph_json
from .ids import object_id, slugify
from .models import (
    OBJECT_TYPES,
    RELATIONSHIP_PREDICATES,
    Cluster,
    KnowledgeObject,
    RawMention,
    ReviewState,
)
from .prompts import make_merge_judge
from .resolution import (
    ResolutionConfig,
    cluster_mentions,
    duplicate_candidate_pairs,
    normalize_name,
    similarity,
)
from .scoring import ScoringConfig, ScoringInputs, score_object
from .service import ConsolidationStats, consolidate, review_object

__all__ = [
    # models
    "OBJECT_TYPES",
    "RELATIONSHIP_PREDICATES",
    "Cluster",
    "KnowledgeObject",
    "RawMention",
    "ReviewState",
    # ids
    "object_id",
    "slugify",
    # resolution
    "ResolutionConfig",
    "cluster_mentions",
    "duplicate_candidate_pairs",
    "normalize_name",
    "similarity",
    # scoring
    "ScoringConfig",
    "ScoringInputs",
    "score_object",
    # service
    "ConsolidationStats",
    "consolidate",
    "review_object",
    # prompts
    "make_merge_judge",
    # analytics
    "top_by_type",
    "most_mentioned",
    "most_connected",
    "conflicting_evidence",
    "duplicate_candidates",
    # export
    "build_nodes",
    "build_edges",
    "export_graph_json",
]
