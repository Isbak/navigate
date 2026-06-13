"""Library-level tests for graph exploration, metrics, health and exports."""

import json

import networkx as nx

from catalog.db import connect
from catalog.graph import network
from catalog.graph.client import GraphClient
from catalog.graph.domains import analyze_domains
from catalog.graph.export import (
    export_gexf,
    export_graphml,
    export_json,
    export_visualization,
)
from catalog.graph.health import knowledge_health
from catalog.graph.loader import id_to_uri, uri_to_id


def _client(db):
    with connect(db) as conn:
        return GraphClient.from_sqlite(conn)


def test_uri_id_roundtrip():
    for object_id in ("capability_release_governance", "platform_salesforce",
                      "team_test_release_team", "capability_release_governance_2"):
        assert uri_to_id(id_to_uri(object_id)) == object_id


def test_build_digraph_nodes_and_edges(approved_graph):
    graph = network.build_digraph(_client(approved_graph.db))
    assert graph.number_of_nodes() == 5
    assert graph.number_of_edges() == 5
    assert graph.nodes["capability_release_governance"]["type"] == "Capability"
    assert graph.nodes["capability_release_governance"]["label"] == "Release Governance"
    # Evidence provenance nodes must not leak into the object graph.
    assert all("evidence" not in n for n in graph.nodes)


def test_shortest_path_direct(approved_graph):
    graph = network.build_digraph(_client(approved_graph.db))
    hops = network.shortest_path(
        graph, "capability_release_governance", "decision_launchpad_model"
    )
    assert len(hops) == 1
    assert hops[0]["predicate"] == "supports"
    assert hops[0]["forward"] is True


def test_shortest_path_multi_hop(approved_graph):
    graph = network.build_digraph(_client(approved_graph.db))
    hops = network.shortest_path(
        graph, "team_test_release_team", "platform_salesforce"
    )
    assert hops is not None
    chain = [hops[0]["from"]] + [h["to"] for h in hops]
    assert chain[0] == "team_test_release_team"
    assert chain[-1] == "platform_salesforce"
    # The path must traverse a real chain of relationships.
    assert len(hops) == len(chain) - 1


def test_shortest_path_missing_node(approved_graph):
    graph = network.build_digraph(_client(approved_graph.db))
    assert network.shortest_path(graph, "nope", "decision_launchpad_model") is None


def test_neighbors_grouped_by_predicate(approved_graph):
    graph = network.build_digraph(_client(approved_graph.db))
    grouped = network.neighbors(graph, "capability_release_governance")
    assert set(grouped) == {"supports", "related_to", "owned_by"}
    supports = grouped["supports"]
    assert supports[0]["id"] == "decision_launchpad_model"
    assert supports[0]["direction"] == "out"


def test_impact_grouped_by_type(approved_graph):
    graph = network.build_digraph(_client(approved_graph.db))
    grouped = network.impact(graph, "capability_release_governance")
    # Release Governance connects to a Decision, a Capability and a Team.
    assert set(grouped) == {"Decision", "Capability", "Team"}
    assert grouped["Team"][0]["label"] == "Test & Release Team"


def test_compute_metrics_central_nodes(approved_graph):
    graph = network.build_digraph(_client(approved_graph.db))
    metrics = network.compute_metrics(graph)
    assert metrics["node_count"] == 5
    assert metrics["edge_count"] == 5
    assert metrics["connected_components"] == 1
    assert 0.0 < metrics["density"] <= 1.0
    top_ids = {n["id"] for n in metrics["top"][:2]}
    # The two capabilities are the hubs of this graph.
    assert "capability_release_governance" in top_ids
    assert metrics["top"][0]["degree"] >= metrics["top"][-1]["degree"]


def test_domains_analysis(approved_graph):
    domains = analyze_domains(_client(approved_graph.db))
    by_name = {d["domain"]: d for d in domains}
    assert by_name["Capability"]["object_count"] == 2
    assert by_name["Capability"]["relationship_count"] >= 1
    assert by_name["Capability"]["most_central"][0]["degree"] >= 1


def test_health_report(approved_graph):
    with connect(approved_graph.db) as conn:
        report = knowledge_health(conn)
    assert report["object_count"] == 5
    assert report["relationship_count"] == 5
    # Every approved object is connected and has evidence in this seed.
    assert report["objects_without_relationships"] == []
    assert report["objects_without_evidence"] == []
    assert len(report["disconnected_subgraphs"]) == 1
    assert report["most_connected"][0]["degree"] >= 1


def test_health_flags_isolated_and_evidenceless(approved_graph):
    # Add an approved object with no relationships and no evidence.
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with connect(approved_graph.db) as conn:
        conn.execute(
            "INSERT INTO knowledge_objects(id, name, object_type, description, "
            "canonical_name, confidence, status, merge_confidence, created_at, updated_at) "
            "VALUES ('concept_orphan', 'Orphan', 'Concept', '', 'Orphan', 0.2, "
            "'APPROVED', 1.0, ?, ?)",
            (now, now),
        )
        conn.commit()
        report = knowledge_health(conn)
    ids_without_rel = {o["id"] for o in report["objects_without_relationships"]}
    ids_without_ev = {o["id"] for o in report["objects_without_evidence"]}
    low_conf_ids = {o["id"] for o in report["low_confidence_objects"]}
    assert "concept_orphan" in ids_without_rel
    assert "concept_orphan" in ids_without_ev
    assert "concept_orphan" in low_conf_ids
    assert len(report["disconnected_subgraphs"]) >= 2


def test_export_formats_roundtrip(approved_graph, tmp_path):
    graph = network.build_digraph(_client(approved_graph.db))

    gexf = export_gexf(graph, tmp_path / "g.gexf")
    graphml = export_graphml(graph, tmp_path / "g.graphml")
    js = export_json(graph, tmp_path / "g.json")

    assert nx.read_gexf(str(gexf)).number_of_nodes() == 5
    assert nx.read_graphml(str(graphml)).number_of_edges() == 5
    payload = json.loads(js.read_text())
    assert len(payload["nodes"]) == 5
    assert len(payload["links"]) == 5


def test_interactive_explorer_drilldown(approved_graph, capsys):
    from catalog.graph.explorer import run_explorer

    commands = iter([
        "search release",
        "show capability_release_governance",
        "neighbors capability_release_governance",
        "evidence capability_release_governance",
        "quit",
    ])
    run_explorer(_client(approved_graph.db), input_fn=lambda _prompt: next(commands))
    out = capsys.readouterr().out
    assert "Release Governance" in out
    assert "Launchpad Model" in out


def test_export_visualization_bundle(approved_graph, tmp_path):
    graph = network.build_digraph(_client(approved_graph.db))
    paths = export_visualization(graph, tmp_path / "viz")
    nodes = json.loads(paths["nodes"].read_text())
    edges = json.loads(paths["edges"].read_text())
    metrics = json.loads(paths["metrics"].read_text())
    assert len(nodes) == 5
    assert len(edges) == 5
    assert metrics["node_count"] == 5
    assert "top" in metrics
    assert all("betweenness" in n for n in nodes)
