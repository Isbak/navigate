"""NetworkX view of the approved knowledge graph, plus graph algorithms.

The directed graph is built entirely through the :class:`GraphClient`, i.e. via
SPARQL, so it works identically whether the client is backed by a local rdflib
graph or a live Fuseki dataset. Nodes are keyed by their stable object id (e.g.
``capability_release_governance``) and carry ``label``/``type``/``confidence``;
edges carry the snake_case ``predicate``.

The algorithms here answer the prompt's exploration questions:

* :func:`shortest_path`   - "shortest path between Team A and Capability B"
* :func:`neighbors`       - connected objects grouped by relationship
* :func:`impact`          - what may be affected, grouped by object type
* :func:`compute_metrics` - degree/betweenness centrality, components, density
"""

from __future__ import annotations

import networkx as nx

from .client import GraphClient
from .loader import local_name, predicate_label, uri_to_id

_PREFIXES = (
    "PREFIX kg: <https://knowledge-atlas.local/kg/>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
)

# Instances only: every kg-typed subject that is not an Evidence provenance node.
NODES_QUERY = _PREFIXES + """
SELECT ?s ?type ?label ?confidence WHERE {
    ?s a ?type .
    FILTER(STRSTARTS(STR(?type), "https://knowledge-atlas.local/kg/"))
    FILTER(?type != kg:Evidence)
    OPTIONAL { ?s rdfs:label ?label }
    OPTIONAL { ?s kg:confidence ?confidence }
}
"""

EDGES_QUERY = _PREFIXES + """
SELECT ?s ?p ?o WHERE {
    ?s ?p ?o .
    FILTER(?p IN (
        kg:supports, kg:dependsOn, kg:implements, kg:affects,
        kg:relatedTo, kg:ownedBy, kg:mentions, kg:references
    ))
}
"""


def build_digraph(client: GraphClient) -> nx.DiGraph:
    """Build a directed graph of approved objects and their relationships."""

    graph = nx.DiGraph()
    for row in client.execute_query(NODES_QUERY):
        node_id = uri_to_id(row["s"])
        confidence = float(row["confidence"]) if row.get("confidence") else 0.0
        graph.add_node(
            node_id,
            label=row.get("label") or node_id,
            type=local_name(row["type"]),
            confidence=confidence,
        )
    for row in client.execute_query(EDGES_QUERY):
        src = uri_to_id(row["s"])
        tgt = uri_to_id(row["o"])
        # Both endpoints are APPROVED (only approved triples are projected), so
        # they are already nodes; guard anyway to never invent attribute-less ones.
        if src in graph and tgt in graph:
            graph.add_edge(src, tgt, predicate=predicate_label(row["p"]))
    return graph


def label_of(graph: nx.DiGraph, node_id: str) -> str:
    """Human label for a node id, falling back to the id itself."""

    if node_id in graph.nodes:
        return graph.nodes[node_id].get("label", node_id)
    return node_id


def shortest_path(graph: nx.DiGraph, source: str, target: str) -> list[dict] | None:
    """Shortest path between two objects, ignoring edge direction.

    Returns an ordered list of hops ``{from, to, predicate, forward}`` where
    ``forward`` is True if the stored relationship points from ``from`` to ``to``.
    Returns ``[]`` if source == target, or ``None`` if either node is missing or
    no path exists.
    """

    if source not in graph or target not in graph:
        return None
    if source == target:
        return []
    undirected = graph.to_undirected(as_view=True)
    try:
        node_path = nx.shortest_path(undirected, source, target)
    except nx.NetworkXNoPath:
        return None

    hops: list[dict] = []
    for a, b in zip(node_path, node_path[1:], strict=False):
        if graph.has_edge(a, b):
            predicate = graph.edges[a, b]["predicate"]
            forward = True
        else:  # the stored edge runs the other way
            predicate = graph.edges[b, a]["predicate"]
            forward = False
        hops.append({"from": a, "to": b, "predicate": predicate, "forward": forward})
    return hops


def neighbors(graph: nx.DiGraph, node_id: str) -> dict[str, list[dict]]:
    """Connected objects grouped by relationship predicate.

    Each entry is ``{id, label, type, direction}`` where ``direction`` is
    ``"out"`` (node is the subject) or ``"in"`` (node is the object).
    """

    grouped: dict[str, list[dict]] = {}
    if node_id not in graph:
        return grouped
    for _, tgt, data in graph.out_edges(node_id, data=True):
        grouped.setdefault(data["predicate"], []).append(
            {"id": tgt, "label": label_of(graph, tgt),
             "type": graph.nodes[tgt].get("type", ""), "direction": "out"}
        )
    for src, _, data in graph.in_edges(node_id, data=True):
        grouped.setdefault(data["predicate"], []).append(
            {"id": src, "label": label_of(graph, src),
             "type": graph.nodes[src].get("type", ""), "direction": "in"}
        )
    return grouped


def impact(graph: nx.DiGraph, node_id: str) -> dict[str, list[dict]]:
    """Directly connected objects grouped by their object type.

    Answers "what may be affected by changes to this object": every neighbour
    (either direction), bucketed by type (Capability, Decision, Team, ...).
    """

    grouped: dict[str, list[dict]] = {}
    if node_id not in graph:
        return grouped
    seen: set[str] = set()
    for other in set(graph.successors(node_id)) | set(graph.predecessors(node_id)):
        if other in seen:
            continue
        seen.add(other)
        otype = graph.nodes[other].get("type", "Unknown")
        grouped.setdefault(otype, []).append(
            {"id": other, "label": label_of(graph, other)}
        )
    for items in grouped.values():
        items.sort(key=lambda d: d["label"].lower())
    return grouped


def object_types(graph: nx.DiGraph) -> list[dict]:
    """Distinct object types in the graph, with member counts, sorted by name."""

    counts: dict[str, int] = {}
    for _, data in graph.nodes(data=True):
        counts[data.get("type", "Unknown")] = counts.get(data.get("type", "Unknown"), 0) + 1
    return [{"name": t, "count": c} for t, c in sorted(counts.items())]


def nodes_by_type(graph: nx.DiGraph, type_filter: str | None = None) -> list[dict]:
    """All approved objects, optionally filtered by type, sorted by label.

    Each entry carries ``id``, ``label``, ``type``, and ``confidence`` so callers
    can page through without fetching full object detail.
    """

    result: list[dict] = []
    for node_id, data in graph.nodes(data=True):
        if type_filter is not None and data.get("type", "") != type_filter:
            continue
        result.append(
            {
                "id": node_id,
                "label": data.get("label", node_id),
                "type": data.get("type", ""),
                "confidence": round(data.get("confidence", 0.0), 4),
            }
        )
    result.sort(key=lambda n: n["label"].lower())
    return result


def ego_subgraph(graph: nx.DiGraph, node_id: str, depth: int) -> dict | None:
    """Nodes and edges within *depth* hops of *node_id* (direction-agnostic).

    Returns ``{"nodes": [...], "edges": [...]}`` or ``None`` when *node_id* is
    not in the graph.  Each node entry mirrors :func:`nodes_by_type`; each edge
    entry is ``{"from", "to", "predicate"}``.
    """

    if node_id not in graph:
        return None
    undirected = graph.to_undirected(as_view=True)
    ego = nx.ego_graph(undirected, node_id, radius=depth)
    nodes = [
        {
            "id": n,
            "label": graph.nodes[n].get("label", n),
            "type": graph.nodes[n].get("type", ""),
            "confidence": round(graph.nodes[n].get("confidence", 0.0), 4),
        }
        for n in ego.nodes()
    ]
    nodes.sort(key=lambda n: n["label"].lower())
    edges = [
        {"from": src, "to": tgt, "predicate": data["predicate"]}
        for src, tgt, data in graph.edges(data=True)
        if src in ego and tgt in ego
    ]
    return {"nodes": nodes, "edges": edges}


def compute_metrics(graph: nx.DiGraph, top: int = 10) -> dict:
    """Degree/betweenness centrality, components, density and clusters.

    Centrality is computed on the undirected projection so a hub is ranked by
    how many things it connects, regardless of edge direction (matching the
    "most central concepts" question).
    """

    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()
    if node_count == 0:
        return {
            "node_count": 0, "edge_count": 0, "density": 0.0,
            "connected_components": 0, "clusters": 0, "nodes": [], "top": [],
        }

    undirected = graph.to_undirected()
    degree_centrality = nx.degree_centrality(graph)
    betweenness = nx.betweenness_centrality(undirected, normalized=True)
    components = list(nx.connected_components(undirected))

    cluster_of: dict[str, int] = {}
    try:
        from networkx.algorithms.community import greedy_modularity_communities

        communities = list(greedy_modularity_communities(undirected))
    except Exception:  # very small/empty graphs can defeat community detection
        communities = [set(c) for c in components]
    for index, community in enumerate(communities):
        for member in community:
            cluster_of[member] = index

    nodes: list[dict] = []
    for node_id, data in graph.nodes(data=True):
        nodes.append(
            {
                "id": node_id,
                "label": data.get("label", node_id),
                "type": data.get("type", ""),
                "degree": graph.degree(node_id),
                "degree_centrality": round(degree_centrality.get(node_id, 0.0), 4),
                "betweenness": round(betweenness.get(node_id, 0.0), 4),
                "cluster": cluster_of.get(node_id, -1),
            }
        )
    nodes.sort(key=lambda n: (-n["degree"], -n["betweenness"], n["label"].lower()))

    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "density": round(nx.density(graph), 4),
        "connected_components": len(components),
        "clusters": len(communities),
        "nodes": nodes,
        "top": nodes[:top],
    }


__all__ = [
    "NODES_QUERY",
    "EDGES_QUERY",
    "build_digraph",
    "label_of",
    "shortest_path",
    "neighbors",
    "impact",
    "object_types",
    "nodes_by_type",
    "ego_subgraph",
    "compute_metrics",
]
