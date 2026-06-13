"""CLI tests for the GraphRAG assistant commands (Prompt #9).

These exercise the ``catalog ask / explain / impact / compare / path-reason``
command surface end to end, stubbing only the LLM provider (patched on
``catalog.graphrag.cli.build_provider``) so no network or Fuseki is needed.
"""

from __future__ import annotations

import pytest

from catalog.cli import main
from catalog.semantic.providers.base import BaseLLMProvider


class StubProvider(BaseLLMProvider):
    def generate(self, prompt, *, system=None):
        return "Release Governance supports the Launchpad Model [E1]."


@pytest.fixture(autouse=True)
def _patch_provider(monkeypatch):
    monkeypatch.setattr(
        "catalog.graphrag.cli.build_provider", lambda cfg: StubProvider("stub-model")
    )


def _base(approved_graph):
    return ["--db", approved_graph.db]


def test_ask_command(approved_graph, capsys):
    assert main(_base(approved_graph) + ["ask", "What supports Release Governance?"]) == 0
    out = capsys.readouterr().out
    assert "Release Governance supports the Launchpad Model" in out
    assert "Knowledge objects used:" in out
    assert "Evidence used:" in out
    assert "Documents used:" in out
    assert "Confidence:" in out


def test_ask_show_context(approved_graph, capsys):
    assert main(
        _base(approved_graph) + ["ask", "What supports Release Governance?", "--show-context"]
    ) == 0
    out = capsys.readouterr().out
    assert "--- CONTEXT ---" in out
    assert "KNOWLEDGE OBJECTS:" in out


def test_ask_show_sparql(approved_graph, capsys):
    assert main(
        _base(approved_graph) + ["ask", "What supports Release Governance?", "--show-sparql"]
    ) == 0
    out = capsys.readouterr().out
    assert "--- SPARQL ---" in out
    assert "SELECT" in out


def test_ask_show_evidence(approved_graph, capsys):
    assert main(
        _base(approved_graph) + ["ask", "What supports Release Governance?", "--show-evidence"]
    ) == 0
    out = capsys.readouterr().out
    assert "--- EVIDENCE ---" in out
    assert "doc_a" in out


def test_ask_depth_flag(approved_graph, capsys):
    assert main(
        _base(approved_graph)
        + ["ask", "What is connected to Release Governance?", "--depth", "3", "--show-context"]
    ) == 0
    out = capsys.readouterr().out
    assert "Salesforce" in out  # reachable only at depth >= 2


def test_ask_unknown_object_declines(approved_graph, capsys):
    assert main(_base(approved_graph) + ["ask", "What supports Nonexistent Thing?"]) == 0
    out = capsys.readouterr().out
    assert "No supporting evidence found." in out


def test_explain_command(approved_graph, capsys):
    assert main(_base(approved_graph) + ["explain", "Release Governance"]) == 0
    out = capsys.readouterr().out
    assert "Confidence:" in out
    assert "Knowledge objects used:" in out


def test_impact_command(approved_graph, capsys):
    assert main(_base(approved_graph) + ["impact", "Salesforce"]) == 0
    out = capsys.readouterr().out
    assert "Confidence:" in out


def test_compare_command(approved_graph, capsys):
    assert main(
        _base(approved_graph) + ["compare", "Release Governance", "Release Management"]
    ) == 0
    out = capsys.readouterr().out
    assert "Q:" in out
    assert "Confidence:" in out


def test_path_reason_command(approved_graph, capsys):
    assert main(
        _base(approved_graph) + ["path-reason", "Release Governance", "Salesforce"]
    ) == 0
    out = capsys.readouterr().out
    assert "Release Governance" in out
    assert "Confidence:" in out


def test_model_override_passed_through(approved_graph, capsys, monkeypatch):
    seen = {}

    def _capture(cfg):
        seen["model"] = cfg.model
        return StubProvider(cfg.model)

    monkeypatch.setattr("catalog.graphrag.cli.build_provider", _capture)
    assert main(
        _base(approved_graph) + ["ask", "What supports Release Governance?", "--model", "qwen3:14b"]
    ) == 0
    assert seen["model"] == "qwen3:14b"
