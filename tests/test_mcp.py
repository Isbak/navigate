"""Tests for the MCP grounding server (``catalog mcp``).

The graph-first tools are exercised directly against the ``approved_graph``
fixture (the documented Release Governance example) so they run fully offline,
with no LLM and no MCP runtime required. The ``ask`` tool is tested on its
deterministic paths: the disabled decline, the no-provider decline, and a happy
path driven by a stub provider.
"""

from __future__ import annotations

import pytest

from catalog.cli import build_parser
from catalog.mcp import tools
from catalog.mcp.config import McpSettings


def _settings(approved_graph, *, enable_graphrag: bool = True) -> McpSettings:
    return McpSettings(db_path=approved_graph.db, enable_graphrag=enable_graphrag)


# -- graph-first tools (offline) ----------------------------------------------

def test_search_knowledge_finds_objects(approved_graph):
    result = tools.search_knowledge(_settings(approved_graph), "release")
    labels = {r["label"] for r in result["results"]}
    assert result["count"] == len(result["results"])
    assert "Release Governance" in labels
    assert "Release Management" in labels


def test_get_object_by_id(approved_graph):
    result = tools.get_object(_settings(approved_graph), "capability_release_governance")
    assert result["found"] is True
    assert result["label"] == "Release Governance"
    assert result["type"] == "Capability"
    assert result["evidence_count"] >= 1


def test_get_object_by_name_resolves(approved_graph):
    result = tools.get_object(_settings(approved_graph), "Release Governance")
    assert result["found"] is True
    assert result["id"] == "capability_release_governance"


def test_get_object_unknown_declines(approved_graph):
    result = tools.get_object(_settings(approved_graph), "nonexistent thing")
    assert result["found"] is False
    assert "nonexistent" in result["message"].lower() or result["candidates"] == []


def test_neighbors_grouped_by_predicate(approved_graph):
    result = tools.neighbors(_settings(approved_graph), "capability_release_governance")
    assert result["found"] is True
    predicates = result["neighbors"]
    assert "supports" in predicates
    assert "owned_by" in predicates


def test_impact_grouped_by_type(approved_graph):
    result = tools.impact(_settings(approved_graph), "platform_salesforce")
    assert result["found"] is True
    assert result["total"] >= 1
    # Salesforce affects Release Management (a Capability).
    flattened = {item["label"] for items in result["impact"].values() for item in items}
    assert "Release Management" in flattened


def test_find_path_between_objects(approved_graph):
    result = tools.find_path(
        _settings(approved_graph),
        "capability_release_governance",
        "decision_launchpad_model",
    )
    assert result["found"] is True
    assert result["hop_count"] >= 1
    assert any(hop["predicate"] == "supports" for hop in result["path"])


def test_find_path_unknown_source_declines(approved_graph):
    result = tools.find_path(_settings(approved_graph), "nope", "decision_launchpad_model")
    assert result["found"] is False


def test_evidence_for_object(approved_graph):
    result = tools.evidence_for_object(
        _settings(approved_graph), "capability_release_governance"
    )
    assert result["found"] is True
    assert result["count"] >= 1
    assert all("quote" in e for e in result["evidence"])


# -- ask tool (deterministic paths, offline) ----------------------------------

def test_ask_disabled_declines(approved_graph):
    result = tools.ask(_settings(approved_graph, enable_graphrag=False), "What is X?")
    assert result["available"] is False
    assert result["objects_used"] == []


def test_ask_without_provider_declines(approved_graph, monkeypatch):
    """With no API key, build_provider raises LLMError; ask degrades gracefully."""

    from catalog.semantic.providers import LLMError

    def _boom(_config):
        raise LLMError("Anthropic API key not set")

    monkeypatch.setattr(tools, "build_provider", _boom)
    result = tools.ask(_settings(approved_graph), "What supports Release Governance?")
    assert result["available"] is False
    assert "unavailable" in result["answer"].lower()


def test_ask_with_stub_provider_returns_answer(approved_graph, monkeypatch):
    """A stub provider drives the full retrieve -> answer -> serialize path offline."""

    class _StubProvider:
        model = "stub"
        last_usage = None

        def generate(self, prompt: str, *, system: str | None = None) -> str:
            return "Release Governance supports the Launchpad Model. [E1]"

    monkeypatch.setattr(tools, "build_provider", lambda _config: _StubProvider())
    result = tools.ask(_settings(approved_graph), "What supports Release Governance?")
    assert result["available"] is True
    assert "Launchpad" in result["answer"]
    assert result["confidence"] in {"High", "Medium", "Low"}
    assert any(o["label"] == "Release Governance" for o in result["objects_used"])


# -- CLI wiring ---------------------------------------------------------------

def test_cli_registers_mcp_command():
    args = build_parser().parse_args(["--db", "x.sqlite", "mcp", "--no-graphrag"])
    assert args.command == "mcp"
    assert args.enable_graphrag is False
    assert args.queries_dir == "queries"


def test_cli_mcp_defaults_graphrag_enabled():
    args = build_parser().parse_args(["mcp"])
    assert args.enable_graphrag is True


def test_build_server_requires_mcp_package(approved_graph):
    """build_server should construct when 'mcp' is installed; skip otherwise."""

    pytest.importorskip("mcp")
    from catalog.mcp.server import build_server

    server = build_server(_settings(approved_graph))
    assert server is not None
