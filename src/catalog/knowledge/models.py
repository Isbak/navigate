"""Dataclasses and controlled vocabularies for the knowledge layer.

The knowledge layer consolidates the document-level proposals produced by the
semantic layer (candidate entities, capabilities, decisions, risks,
relationships) into reusable *knowledge objects*. A knowledge object is the
single thing that many documents were independently talking about - the running
example being three documents that each mention "Release Governance" /
"Release Governance Model" / "Release governance" collapsing into one
``capability_release_governance`` object.

Nothing here is RDF, Jena, SPARQL, or GraphRAG. This is the graph-ready
foundation: stable URI-ready ids, typed objects, traceable evidence, and typed
relationships - all carrying confidence and a review status, because the LLM and
the resolver *propose*, and humans *approve*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReviewState(str, Enum):
    """Review workflow for knowledge objects and relationships.

    Distinct from the semantic layer's ``ReviewStatus`` (whose default is NEW):
    consolidated knowledge starts life as PROPOSED and only APPROVED objects and
    relationships are considered trusted.
    """

    PROPOSED = "PROPOSED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


# The object types a knowledge object may take. This is a superset of the
# semantic layer's ENTITY_TYPES (which already aligns with these names) plus the
# Capability/Decision/Risk kinds that arrive from their own candidate tables, and
# the compliance layer's Standard/Requirement kinds that arrive from
# ``candidate_requirements``.
OBJECT_TYPES = (
    "Capability",
    "Initiative",
    "Technology",
    "Platform",
    "Team",
    "Product",
    "Concept",
    "Decision",
    "Risk",
    "Process",
    "Standard",
    "Requirement",
    "Equation",
)

# Predicates a knowledge relationship may use. Mirrors the semantic layer's
# vocabulary so relationships proposed per-document carry over cleanly. The
# trailing four are the compliance predicates: a requirement is ``mandated_by`` a
# standard, a control ``satisfies`` a requirement, a requirement ``specifies`` an
# equation, and an amended standard/requirement ``supersedes`` the one it
# replaces.
RELATIONSHIP_PREDICATES = (
    "supports",
    "depends_on",
    "implements",
    "mentions",
    "references",
    "affects",
    "owned_by",
    "related_to",
    "mandated_by",
    "satisfies",
    "specifies",
    "supersedes",
)


@dataclass(frozen=True)
class RawMention:
    """One occurrence of an entity in a single document, before consolidation.

    Gathered from the semantic ``candidate_*`` tables. ``source_text`` is the
    supporting quote the semantic layer captured, reused here as evidence.
    """

    object_type: str
    name: str
    artifact_id: str
    confidence: float
    source_text: str = ""


@dataclass(frozen=True)
class Cluster:
    """A group of raw mentions judged to refer to the same real-world thing.

    ``canonical_name`` is the representative chosen from the members, and
    ``merge_confidence`` is the cohesion of the cluster (1.0 for an exact-name
    group, the minimum pairwise similarity for a fuzzy-merged group). It is the
    "store merge confidence" requirement: a low value means the merge is shaky.
    """

    object_type: str
    canonical_name: str
    mentions: list[RawMention]
    merge_confidence: float = 1.0

    @property
    def artifact_ids(self) -> set[str]:
        return {m.artifact_id for m in self.mentions}


@dataclass(frozen=True)
class KnowledgeObject:
    """A consolidated, reusable knowledge object."""

    id: str
    name: str
    object_type: str
    description: str
    canonical_name: str
    confidence: float
    status: str = ReviewState.PROPOSED.value
    merge_confidence: float = 1.0


__all__ = [
    "ReviewState",
    "OBJECT_TYPES",
    "RELATIONSHIP_PREDICATES",
    "RawMention",
    "Cluster",
    "KnowledgeObject",
]
