"""Domain analysis over the approved knowledge graph.

A *knowledge domain* here is an object type (Capability, Decision, Team,
Platform, ...): the natural typing of the graph. For each domain this reports

* the number of objects,
* the number of relationships that touch the domain, and
* the most central concept (highest degree) within it.

This answers "what are the knowledge domains and their most central concepts"
directly from the graph, with no LLM or embeddings involved.
"""

from __future__ import annotations

from .client import GraphClient
from .network import build_digraph


def analyze_domains(client: GraphClient) -> list[dict]:
    """Return per-type domain summaries, largest (by object count) first."""

    graph = build_digraph(client)
    undirected_degree = dict(graph.degree)

    by_type: dict[str, list[str]] = {}
    for node_id, data in graph.nodes(data=True):
        by_type.setdefault(data.get("type", "Unknown"), []).append(node_id)

    domains: list[dict] = []
    for object_type, members in by_type.items():
        member_set = set(members)
        # Relationships that touch this domain (either endpoint in the type).
        rel_count = sum(
            1
            for src, tgt in graph.edges()
            if src in member_set or tgt in member_set
        )
        central = sorted(
            members, key=lambda n: (-undirected_degree.get(n, 0),
                                    graph.nodes[n].get("label", n).lower())
        )
        most_central = [
            {
                "id": nid,
                "label": graph.nodes[nid].get("label", nid),
                "degree": undirected_degree.get(nid, 0),
            }
            for nid in central[:5]
        ]
        domains.append(
            {
                "domain": object_type,
                "object_count": len(members),
                "relationship_count": rel_count,
                "most_central": most_central,
            }
        )

    domains.sort(key=lambda d: (-d["object_count"], d["domain"]))
    return domains


__all__ = ["analyze_domains"]
