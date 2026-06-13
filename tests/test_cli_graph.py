"""CLI tests for the ``catalog graph`` knowledge explorer commands (Prompt #8)."""

import json

from catalog.cli import main


def _base(approved_graph):
    return ["--db", approved_graph.db, "graph"]


def test_graph_query_list(approved_graph, capsys):
    assert main(_base(approved_graph) + ["query"]) == 0
    out = capsys.readouterr().out
    assert "all_capabilities" in out
    assert "knowledge_domains" in out


def test_graph_query_named(approved_graph, capsys):
    assert main(_base(approved_graph) + ["query", "all_capabilities"]) == 0
    out = capsys.readouterr().out
    assert "Release Governance" in out
    assert "Release Management" in out


def test_graph_search(approved_graph, capsys):
    assert main(_base(approved_graph) + ["search", "release"]) == 0
    out = capsys.readouterr().out
    assert "Release Governance" in out
    assert "Test & Release Team" in out


def test_graph_show(approved_graph, capsys):
    assert main(_base(approved_graph) + ["show", "capability_release_governance"]) == 0
    out = capsys.readouterr().out
    assert "Release Governance" in out
    assert "Type: Capability" in out
    assert "Launchpad Model" in out
    assert "Test & Release Team" in out
    assert "Evidence count:" in out


def test_graph_path(approved_graph, capsys):
    assert main(
        _base(approved_graph)
        + ["path", "capability_release_governance", "decision_launchpad_model"]
    ) == 0
    out = capsys.readouterr().out
    assert "Release Governance" in out
    assert "supports" in out
    assert "Launchpad Model" in out


def test_graph_neighbors(approved_graph, capsys):
    assert main(_base(approved_graph) + ["neighbors", "capability_release_governance"]) == 0
    out = capsys.readouterr().out
    assert "supports" in out
    assert "owned_by" in out
    assert "Release Management" in out


def test_graph_impact(approved_graph, capsys):
    assert main(_base(approved_graph) + ["impact", "platform_salesforce"]) == 0
    out = capsys.readouterr().out
    assert "Impact of Salesforce" in out
    assert "Release Management" in out


def test_graph_health(approved_graph, capsys):
    assert main(_base(approved_graph) + ["health"]) == 0
    out = capsys.readouterr().out
    assert "Knowledge health" in out
    assert "Most connected nodes" in out
    assert "Disconnected subgraphs" in out


def test_graph_domains(approved_graph, capsys):
    assert main(_base(approved_graph) + ["domains"]) == 0
    out = capsys.readouterr().out
    assert "Capability" in out
    assert "objects" in out


def test_graph_metrics_writes_visualization(approved_graph, tmp_path, capsys):
    out_dir = tmp_path / "viz"
    assert main(_base(approved_graph) + ["--out", str(out_dir), "metrics"]) == 0
    out = capsys.readouterr().out
    assert "Most central knowledge objects" in out
    assert "Degree:" in out
    assert (out_dir / "nodes.json").exists()
    assert (out_dir / "edges.json").exists()
    metrics = json.loads((out_dir / "metrics.json").read_text())
    assert metrics["node_count"] == 5


def test_graph_exports(approved_graph, tmp_path, capsys):
    out_dir = tmp_path / "exp"
    base = ["--db", approved_graph.db, "graph", "--out", str(out_dir)]
    assert main(base + ["export-gexf"]) == 0
    assert main(base + ["export-graphml"]) == 0
    assert main(base + ["export-json"]) == 0
    assert (out_dir / "knowledge.gexf").exists()
    assert (out_dir / "knowledge.graphml").exists()
    assert (out_dir / "graph.json").exists()


def test_graph_show_unknown_object(approved_graph, capsys):
    assert main(_base(approved_graph) + ["show", "no_such_object"]) == 0
    out = capsys.readouterr().out
    assert "No object matching" in out
