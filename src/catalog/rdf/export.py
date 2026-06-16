"""RDF projection of the approved knowledge base (Prompt #7).

Projects APPROVED knowledge objects, APPROVED relationships, and their evidence
into RDF graphs and writes them as separate files:

    ontology.ttl       the schema (static; see ``ontology.py``)
    knowledge.ttl       one resource per approved object
    relationships.ttl   approved object-to-object triples
    provenance.ttl      evidence resources + ``kg:supportedBy`` links

SQLite stays the system of record; this layer is a read-only projection. Only
APPROVED data crosses the boundary - PROPOSED/REVIEWED/REJECTED objects and
relationships never appear in the export. Keeping the four graphs in separate
files keeps the export forward-compatible with named graphs (each file maps
cleanly onto its own graph in a quad store).

Default serialization is Turtle; JSON-LD and N-Triples are also supported.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS

from ..knowledge import repository as repo
from .namespaces import (
    KG,
    SUPPORTED_BY,
    bind_namespaces,
    class_uri,
    evidence_uri,
    object_uri,
    predicate_uri,
)
from .ontology import build_ontology_graph

DEFAULT_OUT_DIR = "exports/rdf"

# format name -> (rdflib serializer, file extension). Turtle is the default.
FORMATS: dict[str, tuple[str, str]] = {
    "turtle": ("turtle", "ttl"),
    "ttl": ("turtle", "ttl"),
    "json-ld": ("json-ld", "jsonld"),
    "jsonld": ("json-ld", "jsonld"),
    "nt": ("nt", "nt"),
    "ntriples": ("nt", "nt"),
    "n-triples": ("nt", "nt"),
}

# Logical graph name -> output filename stem. Extensions are added per-format.
GRAPH_STEMS = ("ontology", "knowledge", "relationships", "provenance")


def _confidence_literal(value) -> Literal:
    """Render a confidence as a tidy xsd:decimal (e.g. ``0.94``)."""

    conf = round(float(value or 0.0), 3)
    return Literal(Decimal(str(conf)))


def _compliance_maps(
    conn: sqlite3.Connection,
) -> tuple[dict[str, sqlite3.Row], dict[str, str]]:
    """Return (requirement metadata, approved compliance status) by object id.

    Both are empty when the compliance tables hold nothing, so the projection is
    unaffected for knowledge bases that do not use the compliance layer.
    """

    meta = {
        row["object_id"]: row
        for row in conn.execute(
            "SELECT object_id, clause_ref, obligation_level FROM compliance_requirements"
        )
    }
    # Project the strongest APPROVED assessment status per requirement.
    _RANK = {"SATISFIED": 3, "PARTIAL": 2, "GAP": 1, "NOT_APPLICABLE": 1}
    status: dict[str, str] = {}
    for row in conn.execute(
        "SELECT requirement_object_id AS rid, status FROM compliance_assessments "
        "WHERE review_status = 'APPROVED'"
    ):
        current = status.get(row["rid"])
        if current is None or _RANK.get(row["status"], 0) > _RANK.get(current, 0):
            status[row["rid"]] = row["status"]
    return meta, status


def build_knowledge_graph(conn: sqlite3.Connection) -> Graph:
    """One RDF resource per APPROVED knowledge object.

    Each object gets ``rdf:type`` of its class, an ``rdfs:label`` (the canonical
    name), ``kg:confidence``, and an ``rdfs:comment`` description when present.
    Requirement objects additionally carry their ``kg:clauseRef``,
    ``kg:obligationLevel``, and (when a human has approved an assessment)
    ``kg:complianceStatus``.
    """

    g = Graph()
    bind_namespaces(g)
    req_meta, status_map = _compliance_maps(conn)
    for row in repo.approved_objects(conn):
        subject = object_uri(row["object_type"], row["id"])
        g.add((subject, RDF.type, class_uri(row["object_type"])))
        g.add((subject, RDFS.label, Literal(row["canonical_name"] or row["name"])))
        g.add((subject, KG["confidence"], _confidence_literal(row["confidence"])))
        if row["description"]:
            g.add((subject, RDFS.comment, Literal(row["description"])))
        meta = req_meta.get(row["id"])
        if meta is not None:
            if meta["clause_ref"]:
                g.add((subject, KG["clauseRef"], Literal(meta["clause_ref"])))
            if meta["obligation_level"]:
                g.add((subject, KG["obligationLevel"], Literal(meta["obligation_level"])))
        if row["id"] in status_map:
            g.add((subject, KG["complianceStatus"], Literal(status_map[row["id"]])))
    return g


def build_relationships_graph(conn: sqlite3.Connection) -> Graph:
    """APPROVED object-to-object triples (both endpoints APPROVED)."""

    g = Graph()
    bind_namespaces(g)
    # The source/target object types are needed to mint the instance URIs.
    types = {
        row["id"]: row["object_type"] for row in repo.approved_objects(conn)
    }
    for row in repo.approved_relationships(conn):
        src_type = types.get(row["source_object"])
        tgt_type = types.get(row["target_object"])
        if src_type is None or tgt_type is None:
            continue
        g.add(
            (
                object_uri(src_type, row["source_object"]),
                predicate_uri(row["predicate"]),
                object_uri(tgt_type, row["target_object"]),
            )
        )
    return g


def build_provenance_graph(conn: sqlite3.Connection) -> Graph:
    """Named evidence resources and the ``kg:supportedBy`` links to objects."""

    g = Graph()
    bind_namespaces(g)
    for row in repo.evidence_for_approved_objects(conn):
        ev = evidence_uri(row["id"])
        g.add((ev, RDF.type, KG["Evidence"]))
        g.add((ev, KG["sourceArtifact"], Literal(row["artifact_id"])))
        g.add((ev, KG["confidence"], _confidence_literal(row["confidence"])))
        if row["quote"]:
            g.add((ev, KG["quote"], Literal(row["quote"])))
        subject = object_uri(row["object_type"], row["object_id"])
        g.add((subject, KG[SUPPORTED_BY], ev))
    return g


def build_graphs(conn: sqlite3.Connection) -> dict[str, Graph]:
    """Build all four graphs keyed by their logical name."""

    return {
        "ontology": build_ontology_graph(),
        "knowledge": build_knowledge_graph(conn),
        "relationships": build_relationships_graph(conn),
        "provenance": build_provenance_graph(conn),
    }


def export_rdf(
    conn: sqlite3.Connection,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    *,
    fmt: str = "turtle",
) -> dict[str, Path]:
    """Build and write the four RDF files. Returns the written paths by name."""

    if fmt not in FORMATS:
        raise ValueError(
            f"Unsupported format {fmt!r}. Choose from: {', '.join(sorted(FORMATS))}"
        )
    serializer, ext = FORMATS[fmt]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    graphs = build_graphs(conn)
    written: dict[str, Path] = {}
    for name in GRAPH_STEMS:
        path = out / f"{name}.{ext}"
        graphs[name].serialize(destination=str(path), format=serializer)
        written[name] = path
    return written


def validate_rdf(out_dir: str | Path = DEFAULT_OUT_DIR) -> dict[str, dict]:
    """Re-parse every exported file with rdflib. Returns per-file results.

    Each value is ``{"ok": bool, "triples": int, "error": str | None}``. A file
    that does not exist is reported as a (non-fatal) error so the caller can flag
    that ``rdf-export`` has not been run yet.
    """

    out = Path(out_dir)
    results: dict[str, dict] = {}
    for path in sorted(out.glob("*")):
        if path.suffix.lstrip(".") not in {ext for _, ext in FORMATS.values()}:
            continue
        result: dict = {"ok": False, "triples": 0, "error": None}
        try:
            g = Graph()
            g.parse(str(path))
            result["ok"] = True
            result["triples"] = len(g)
        except Exception as exc:  # rdflib raises a variety of parse errors
            result["error"] = str(exc)
        results[path.name] = result
    return results


def rdf_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Counts of what an export would contain (objects/relationships/evidence)."""

    knowledge = build_knowledge_graph(conn)
    relationships = build_relationships_graph(conn)
    provenance = build_provenance_graph(conn)
    objects = len(repo.approved_objects(conn))
    evidence = len(repo.evidence_for_approved_objects(conn))
    return {
        "objects": objects,
        "relationships": len(relationships),
        "evidence": evidence,
        "knowledge_triples": len(knowledge),
        "relationship_triples": len(relationships),
        "provenance_triples": len(provenance),
    }


__all__ = [
    "DEFAULT_OUT_DIR",
    "FORMATS",
    "GRAPH_STEMS",
    "build_knowledge_graph",
    "build_relationships_graph",
    "build_provenance_graph",
    "build_graphs",
    "export_rdf",
    "validate_rdf",
    "rdf_stats",
]
