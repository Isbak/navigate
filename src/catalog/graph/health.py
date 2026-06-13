"""Knowledge health checks over the approved graph.

Validation is the whole point of this phase: prove the graph is sound *before*
any AI layer is added. These checks read the SQLite system of record (restricted
to ``APPROVED`` data, to match what the explorer and the RDF projection expose)
and surface the gaps a reviewer should close:

* objects with no relationships          - islands that add no structure
* objects with no evidence               - untraceable claims (invariant breach)
* relationships with no evidence          - links asserted without a source quote
* duplicate candidates                    - probable un-merged objects
* low-confidence objects                  - weakly supported claims
* disconnected subgraphs                  - clusters with no bridge between them
* most connected nodes                    - the load-bearing concepts

Connectivity (subgraphs, most-connected) is computed with NetworkX over the
approved objects and relationships.
"""

from __future__ import annotations

import json
import sqlite3

import networkx as nx

from ..knowledge import analytics as know_analytics
from ..knowledge import repository as repo

# Below this score an approved object is flagged as weakly supported.
LOW_CONFIDENCE_THRESHOLD = 0.5


def _approved_digraph(conn: sqlite3.Connection) -> nx.DiGraph:
    graph = nx.DiGraph()
    for obj in repo.approved_objects(conn):
        graph.add_node(
            obj["id"],
            label=obj["canonical_name"] or obj["name"],
            type=obj["object_type"],
            confidence=obj["confidence"] or 0.0,
        )
    for rel in repo.approved_relationships(conn):
        if rel["source_object"] in graph and rel["target_object"] in graph:
            graph.add_edge(
                rel["source_object"], rel["target_object"],
                predicate=rel["predicate"],
            )
    return graph


def _relationship_has_evidence(raw: object) -> bool:
    if not raw:
        return False
    try:
        return bool(json.loads(raw))
    except (TypeError, ValueError):
        return bool(str(raw).strip())


def knowledge_health(
    conn: sqlite3.Connection, *, low_confidence: float = LOW_CONFIDENCE_THRESHOLD
) -> dict:
    """Compute every health signal and return them in one report dict."""

    objects = repo.approved_objects(conn)
    relationships = repo.approved_relationships(conn)
    graph = _approved_digraph(conn)

    # Objects with no approved relationship touching them.
    connected_ids = set()
    for rel in relationships:
        connected_ids.add(rel["source_object"])
        connected_ids.add(rel["target_object"])
    no_relationships = [
        {"id": o["id"], "name": o["canonical_name"], "type": o["object_type"]}
        for o in objects
        if o["id"] not in connected_ids
    ]

    # Objects with no evidence rows (should be empty by invariant).
    no_evidence = [
        {"id": o["id"], "name": o["canonical_name"], "type": o["object_type"]}
        for o in objects
        if not repo.evidence_for_object(conn, o["id"])
    ]

    # Relationships whose evidence payload is empty.
    rels_without_evidence = [
        {
            "source": r["source_object"],
            "predicate": r["predicate"],
            "target": r["target_object"],
        }
        for r in relationships
        if not _relationship_has_evidence(r["evidence"])
    ]

    # Low-confidence approved objects.
    low_conf = [
        {
            "id": o["id"],
            "name": o["canonical_name"],
            "type": o["object_type"],
            "confidence": round(o["confidence"] or 0.0, 3),
        }
        for o in objects
        if (o["confidence"] or 0.0) < low_confidence
    ]
    low_conf.sort(key=lambda d: d["confidence"])

    # Disconnected subgraphs (weakly connected components), largest first.
    components = sorted(
        (sorted(c) for c in nx.connected_components(graph.to_undirected())),
        key=len,
        reverse=True,
    )
    subgraphs = [
        {
            "size": len(component),
            "members": [
                {"id": nid, "label": graph.nodes[nid].get("label", nid)}
                for nid in component
            ],
        }
        for component in components
    ]

    # Most connected nodes by degree.
    degrees = sorted(graph.degree, key=lambda kv: (-kv[1], kv[0]))
    most_connected = [
        {"id": nid, "label": graph.nodes[nid].get("label", nid), "degree": deg}
        for nid, deg in degrees
        if deg > 0
    ][:10]

    # Probable un-merged objects among approved nodes (reuses the consolidation
    # duplicate detector, then keeps only pairs both visible in the graph).
    duplicates = [
        d
        for d in know_analytics.duplicate_candidates(conn, limit=50)
        if d["left_id"] in graph and d["right_id"] in graph
    ][:10]

    return {
        "object_count": len(objects),
        "relationship_count": len(relationships),
        "objects_without_relationships": no_relationships,
        "objects_without_evidence": no_evidence,
        "relationships_without_evidence": rels_without_evidence,
        "low_confidence_objects": low_conf,
        "duplicate_candidates": duplicates,
        "disconnected_subgraphs": subgraphs,
        "most_connected": most_connected,
    }


__all__ = ["LOW_CONFIDENCE_THRESHOLD", "knowledge_health"]
