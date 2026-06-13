"""Export the knowledge graph for external tools and visualization.

Three interchange formats are written from the NetworkX view:

* **GEXF**    - for Gephi.
* **GraphML** - for yEd, Cytoscape, and most graph tools.
* **JSON**    - node-link JSON for NetworkX / D3 / custom viewers.

Plus a *visualization bundle* (``nodes.json`` / ``edges.json`` / ``metrics.json``)
under ``exports/graph/`` that carries the computed centrality so a viewer can
size and colour nodes without recomputing anything. The Neo4j import path is the
GraphML file (``apoc.import.graphml``).
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from .network import compute_metrics

DEFAULT_OUT_DIR = "exports/graph"


def export_gexf(graph: nx.DiGraph, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    nx.write_gexf(graph, str(out))
    return out


def export_graphml(graph: nx.DiGraph, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(graph, str(out))
    return out


def graph_to_node_link(graph: nx.DiGraph) -> dict:
    """NetworkX node-link representation (stable ``links`` key)."""

    return nx.node_link_data(graph, edges="links")


def export_json(graph: nx.DiGraph, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(graph_to_node_link(graph), indent=2), encoding="utf-8"
    )
    return out


def export_visualization(
    graph: nx.DiGraph, out_dir: str | Path = DEFAULT_OUT_DIR
) -> dict[str, Path]:
    """Write ``nodes.json`` / ``edges.json`` / ``metrics.json`` for a viewer.

    ``nodes.json`` carries per-node centrality (degree, betweenness, cluster) so
    a visualization can size/colour without recomputation; ``edges.json`` lists
    typed relationships; ``metrics.json`` holds graph-level statistics and the
    most central nodes.
    """

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    metrics = compute_metrics(graph)
    by_id = {node["id"]: node for node in metrics["nodes"]}

    nodes = [
        {
            "id": node_id,
            "label": data.get("label", node_id),
            "type": data.get("type", ""),
            "confidence": data.get("confidence", 0.0),
            "degree": by_id.get(node_id, {}).get("degree", 0),
            "degree_centrality": by_id.get(node_id, {}).get("degree_centrality", 0.0),
            "betweenness": by_id.get(node_id, {}).get("betweenness", 0.0),
            "cluster": by_id.get(node_id, {}).get("cluster", -1),
        }
        for node_id, data in graph.nodes(data=True)
    ]
    edges = [
        {"source": src, "target": tgt, "predicate": data.get("predicate", "")}
        for src, tgt, data in graph.edges(data=True)
    ]
    summary = {
        "node_count": metrics["node_count"],
        "edge_count": metrics["edge_count"],
        "density": metrics["density"],
        "connected_components": metrics["connected_components"],
        "clusters": metrics["clusters"],
        "top": metrics["top"],
    }

    paths = {
        "nodes": out / "nodes.json",
        "edges": out / "edges.json",
        "metrics": out / "metrics.json",
    }
    paths["nodes"].write_text(json.dumps(nodes, indent=2), encoding="utf-8")
    paths["edges"].write_text(json.dumps(edges, indent=2), encoding="utf-8")
    paths["metrics"].write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return paths


__all__ = [
    "DEFAULT_OUT_DIR",
    "export_gexf",
    "export_graphml",
    "export_json",
    "graph_to_node_link",
    "export_visualization",
]
