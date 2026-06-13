"""Graph-ready JSON export.

Writes ``nodes.json`` and ``edges.json`` describing the consolidated knowledge
graph for a *future* visualization layer. This phase deliberately builds no UI;
it only produces the data a UI (or a later RDF mapping) would consume.

Nodes are the knowledge objects; edges are the relationships between them.
REJECTED objects and relationships are excluded, but PROPOSED ones are included
with their status so a viewer can distinguish trusted (APPROVED) from candidate
links. The stable, URI-ready object ids are used verbatim as node ids, which is
exactly what a later RDF resource mapping will key on.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import ReviewState

NODES_FILENAME = "nodes.json"
EDGES_FILENAME = "edges.json"


def build_nodes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT o.id, o.canonical_name, o.object_type, o.confidence, o.status,
               COUNT(DISTINCT m.id) AS mentions,
               COUNT(DISTINCT m.artifact_id) AS documents
        FROM knowledge_objects o
        LEFT JOIN knowledge_mentions m ON m.knowledge_object_id = o.id
        WHERE o.status != ?
        GROUP BY o.id
        ORDER BY o.id
        """,
        (ReviewState.REJECTED.value,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "label": r["canonical_name"],
            "type": r["object_type"],
            "confidence": r["confidence"],
            "status": r["status"],
            "documents": r["documents"],
            "mentions": r["mentions"],
        }
        for r in rows
    ]


def build_edges(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT r.id, r.source_object, r.predicate, r.target_object,
               r.confidence, r.review_status
        FROM knowledge_relationships r
        JOIN knowledge_objects s ON s.id = r.source_object AND s.status != ?
        JOIN knowledge_objects t ON t.id = r.target_object AND t.status != ?
        WHERE r.review_status != ?
        ORDER BY r.id
        """,
        (ReviewState.REJECTED.value,) * 3,
    ).fetchall()
    return [
        {
            "id": r["id"],
            "source": r["source_object"],
            "target": r["target_object"],
            "predicate": r["predicate"],
            "confidence": r["confidence"],
            "status": r["review_status"],
        }
        for r in rows
    ]


def export_graph_json(
    conn: sqlite3.Connection, out_dir: str | Path = "exports/graph"
) -> dict[str, Path]:
    """Write ``nodes.json`` and ``edges.json`` to ``out_dir``.

    Returns the two written paths keyed ``"nodes"`` and ``"edges"``.
    """

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    nodes = build_nodes(conn)
    edges = build_edges(conn)

    nodes_path = out / NODES_FILENAME
    edges_path = out / EDGES_FILENAME
    nodes_path.write_text(json.dumps(nodes, indent=2), encoding="utf-8")
    edges_path.write_text(json.dumps(edges, indent=2), encoding="utf-8")

    return {"nodes": nodes_path, "edges": edges_path}


__all__ = [
    "NODES_FILENAME",
    "EDGES_FILENAME",
    "build_nodes",
    "build_edges",
    "export_graph_json",
]
