"""The knowledge-graph ontology (``ontology.ttl``).

Defines the ten object classes and the relationship predicates the projection
uses, plus the small provenance vocabulary (``kg:Evidence``, ``kg:supportedBy``,
``kg:sourceArtifact``, ``kg:quote``, ``kg:confidence``). The ontology is static -
it does not depend on the database - so a fresh Fuseki dataset can be seeded with
the schema before any instance data is loaded.

Classes are declared as ``owl:Class`` (also ``rdfs:Class`` for plain-RDFS
consumers) and predicates as ``owl:ObjectProperty`` / ``owl:DatatypeProperty``.
"""

from __future__ import annotations

from rdflib import Graph, Literal
from rdflib.namespace import OWL, RDF, RDFS, XSD

from ..knowledge.models import OBJECT_TYPES
from .namespaces import KG, PREDICATE_MAP, SUPPORTED_BY, bind_namespaces

# Human-friendly labels and comments for the ten object classes.
_CLASS_COMMENTS = {
    "Capability": "A business or technical capability the organization possesses.",
    "Initiative": "A program or effort directed at a goal.",
    "Technology": "A concrete technology, tool, or framework.",
    "Platform": "A platform or system that hosts capabilities.",
    "Team": "A group of people that owns or operates something.",
    "Product": "A product offered internally or externally.",
    "Concept": "A domain concept or idea.",
    "Decision": "A recorded decision and its rationale.",
    "Risk": "A risk, threat, or concern.",
    "Process": "A repeatable process or workflow.",
}

# Relationship predicate -> (label, comment). Keyed by the camelCase ontology
# names (the values of PREDICATE_MAP).
_PREDICATE_COMMENTS = {
    "supports": "The subject supports the object.",
    "dependsOn": "The subject depends on the object.",
    "implements": "The subject implements the object.",
    "affects": "The subject affects the object.",
    "relatedTo": "The subject is related to the object.",
    "ownedBy": "The subject is owned by the object.",
    "mentions": "The subject mentions the object.",
    "references": "The subject references the object.",
}


def _label_from_camel(name: str) -> str:
    """Turn ``dependsOn`` into ``depends on`` for a readable rdfs:label."""

    out = [name[0]]
    for ch in name[1:]:
        if ch.isupper():
            out.append(" ")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def build_ontology_graph() -> Graph:
    """Build the ontology graph: classes, predicates, and provenance vocabulary."""

    g = Graph()
    bind_namespaces(g)

    g.add((KG[""], RDF.type, OWL.Ontology))
    g.add((KG[""], RDFS.label, Literal("Knowledge Atlas ontology")))

    # Object classes.
    for object_type in OBJECT_TYPES:
        cls = KG[object_type]
        g.add((cls, RDF.type, OWL.Class))
        g.add((cls, RDF.type, RDFS.Class))
        g.add((cls, RDFS.label, Literal(object_type)))
        g.add((cls, RDFS.comment, Literal(_CLASS_COMMENTS[object_type])))

    # Relationship predicates (object properties).
    for camel in dict.fromkeys(PREDICATE_MAP.values()):
        prop = KG[camel]
        g.add((prop, RDF.type, OWL.ObjectProperty))
        g.add((prop, RDF.type, RDF.Property))
        g.add((prop, RDFS.label, Literal(_label_from_camel(camel))))
        g.add((prop, RDFS.comment, Literal(_PREDICATE_COMMENTS[camel])))

    # Provenance vocabulary.
    evidence = KG["Evidence"]
    g.add((evidence, RDF.type, OWL.Class))
    g.add((evidence, RDF.type, RDFS.Class))
    g.add((evidence, RDFS.label, Literal("Evidence")))
    g.add((evidence, RDFS.comment, Literal("A traceable quote supporting a knowledge object.")))

    supported_by = KG[SUPPORTED_BY]
    g.add((supported_by, RDF.type, OWL.ObjectProperty))
    g.add((supported_by, RDF.type, RDF.Property))
    g.add((supported_by, RDFS.label, Literal("supported by")))
    g.add((supported_by, RDFS.comment, Literal("Links a knowledge object to supporting evidence.")))

    # Datatype properties shared by instances and evidence.
    for name, label, comment, rng in (
        ("confidence", "confidence", "Confidence score in [0.0, 1.0].", XSD.decimal),
        ("sourceArtifact", "source artifact", "The artifact id the evidence came from.", XSD.string),
        ("quote", "quote", "The supporting quote text.", XSD.string),
    ):
        prop = KG[name]
        g.add((prop, RDF.type, OWL.DatatypeProperty))
        g.add((prop, RDF.type, RDF.Property))
        g.add((prop, RDFS.label, Literal(label)))
        g.add((prop, RDFS.comment, Literal(comment)))
        g.add((prop, RDFS.range, rng))

    return g


__all__ = ["build_ontology_graph"]
