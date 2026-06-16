"""URIs and namespaces for the RDF projection (Prompt #7).

This module is the single source of truth for *how a knowledge object becomes
an RDF resource*. SQLite remains the system of record; these helpers only map
the already-stable ids the consolidation layer produced into stable URIs.

URI strategy
------------
Base namespace::

    https://knowledge-atlas.local/kg/

Classes and predicates are prefixed names under that base, e.g. ``kg:Capability``
and ``kg:supports`` -> ``https://knowledge-atlas.local/kg/Capability``.

Instances live under a per-type path segment, matching the documented examples::

    https://knowledge-atlas.local/kg/capability/release_governance
    https://knowledge-atlas.local/kg/team/test_release
    https://knowledge-atlas.local/kg/decision/launchpad_model
    https://knowledge-atlas.local/kg/platform/salesforce

The local name is derived from the object's *stable* id (``capability_release_governance``)
rather than recomputed from its name, so collision-suffixed ids
(``capability_release_governance_2``) stay unique and stable across exports. No
random ids are ever minted: the same knowledge object always yields the same URI.
"""

from __future__ import annotations

from rdflib import Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from ..knowledge.ids import slugify

# The base namespace for every resource in the knowledge graph projection.
BASE = "https://knowledge-atlas.local/kg/"
KG = Namespace(BASE)

# Re-export the standard vocabularies the rest of the package serializes with.
__all_vocab__ = (RDF, RDFS, OWL, XSD)

# Snake_case predicates as stored in SQLite -> the camelCase ontology predicate.
# Mirrors knowledge.models.RELATIONSHIP_PREDICATES on the left and the ontology's
# declared predicates on the right.
PREDICATE_MAP = {
    "supports": "supports",
    "depends_on": "dependsOn",
    "implements": "implements",
    "mentions": "mentions",
    "references": "references",
    "affects": "affects",
    "owned_by": "ownedBy",
    "related_to": "relatedTo",
    "mandated_by": "mandatedBy",
    "satisfies": "satisfies",
    "supersedes": "supersedes",
}

# Provenance links a knowledge object to the evidence that supports it.
SUPPORTED_BY = "supportedBy"


def class_uri(object_type: str) -> URIRef:
    """Return the ontology class URI for a knowledge object type.

    >>> class_uri("Capability")
    rdflib.term.URIRef('https://knowledge-atlas.local/kg/Capability')
    """

    return KG[object_type]


def predicate_uri(predicate: str) -> URIRef:
    """Map a stored snake_case predicate to its ontology URI.

    Unknown predicates fall back to their literal form so nothing is silently
    dropped, but every value in ``RELATIONSHIP_PREDICATES`` is covered.
    """

    return KG[PREDICATE_MAP.get(predicate, predicate)]


def object_local_name(object_type: str, object_id: str) -> str:
    """Strip the ``<type>_`` prefix the id encodes, leaving the name slug.

    >>> object_local_name("Capability", "capability_release_governance")
    'release_governance'
    >>> object_local_name("Capability", "capability_release_governance_2")
    'release_governance_2'
    """

    prefix = f"{slugify(object_type)}_"
    if object_id.startswith(prefix):
        return object_id[len(prefix):]
    return object_id


def object_uri(object_type: str, object_id: str) -> URIRef:
    """Return the stable instance URI ``kg/<type>/<name>`` for an object.

    >>> object_uri("Capability", "capability_release_governance")
    rdflib.term.URIRef('https://knowledge-atlas.local/kg/capability/release_governance')
    """

    return KG[f"{slugify(object_type)}/{object_local_name(object_type, object_id)}"]


def evidence_uri(evidence_id: int | str) -> URIRef:
    """Return the stable URI for a provenance evidence resource.

    >>> evidence_uri(123)
    rdflib.term.URIRef('https://knowledge-atlas.local/kg/evidence/123')
    """

    return KG[f"evidence/{evidence_id}"]


def bind_namespaces(graph) -> None:
    """Bind the conventional prefixes so serializations are readable."""

    graph.bind("kg", KG)
    graph.bind("rdfs", RDFS)
    graph.bind("owl", OWL)
    graph.bind("xsd", XSD)


__all__ = [
    "BASE",
    "KG",
    "PREDICATE_MAP",
    "SUPPORTED_BY",
    "class_uri",
    "predicate_uri",
    "object_local_name",
    "object_uri",
    "evidence_uri",
    "bind_namespaces",
]
