"""Build the in-memory RDF graph the explorer queries, and map URIs <-> ids.

The Knowledge Explorer reads the **approved** knowledge graph. By default it
does not require a running Fuseki: it reconstructs the same RDF projection the
``catalog.rdf`` layer would export (knowledge + relationships + provenance) into
an in-memory :class:`rdflib.Graph` straight from SQLite, then runs real SPARQL
against it. Pointing at a live Fuseki endpoint instead changes only *where* the
SPARQL runs, not the queries.

The ontology graph is deliberately *not* loaded: it would add ``owl:Class``
resources (``kg:Capability a owl:Class``) that would otherwise show up as nodes.
Only instance data (typed objects), their relationships, and their evidence
participate in exploration.

URI <-> id mapping
------------------
A knowledge object's stable id encodes its type as a single leading token, e.g.
``capability_release_governance`` -> ``kg/capability/release_governance``. Object
type slugs never contain ``_`` (the ten types are single words), so the first
separator is unambiguous and the round-trip is exact, including collision
suffixes (``..._2``).
"""

from __future__ import annotations

import sqlite3

from rdflib import Graph

from ..rdf.export import (
    build_knowledge_graph,
    build_provenance_graph,
    build_relationships_graph,
)
from ..rdf.namespaces import BASE, PREDICATE_MAP, bind_namespaces

# snake_case predicate (as stored / displayed) <- camelCase ontology predicate.
REVERSE_PREDICATE_MAP = {camel: snake for snake, camel in PREDICATE_MAP.items()}


def build_graph(conn: sqlite3.Connection) -> Graph:
    """Reconstruct the approved RDF projection in memory (no Fuseki required).

    Combines the knowledge, relationships, and provenance graphs the export
    layer produces. Only ``APPROVED`` objects/relationships are included, exactly
    matching what ``catalog rdf-export`` writes.
    """

    g = Graph()
    bind_namespaces(g)
    for part in (
        build_knowledge_graph(conn),
        build_relationships_graph(conn),
        build_provenance_graph(conn),
    ):
        for triple in part:
            g.add(triple)
    return g


def uri_to_id(uri: str) -> str:
    """Map a ``kg`` instance URI back to its stable object id.

    >>> uri_to_id("https://knowledge-atlas.local/kg/capability/release_governance")
    'capability_release_governance'
    """

    if uri.startswith(BASE):
        rest = uri[len(BASE):]
        # type/name -> type_name (only the type separator is rewritten).
        return rest.replace("/", "_", 1)
    return uri


def id_to_uri(object_id: str) -> str:
    """Map a stable object id to its ``kg`` instance URI.

    >>> id_to_uri("platform_salesforce")
    'https://knowledge-atlas.local/kg/platform/salesforce'
    """

    return BASE + object_id.replace("_", "/", 1)


def local_name(uri: str) -> str:
    """Return the trailing path/fragment segment of a URI (``.../X`` -> ``X``)."""

    return uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def predicate_label(uri: str) -> str:
    """Map a predicate URI to its snake_case display form (``dependsOn`` -> ``depends_on``)."""

    camel = local_name(uri)
    return REVERSE_PREDICATE_MAP.get(camel, camel)


__all__ = [
    "REVERSE_PREDICATE_MAP",
    "build_graph",
    "uri_to_id",
    "id_to_uri",
    "local_name",
    "predicate_label",
]
