"""Tests for the SPARQL query layer: GraphClient and the query library."""

from catalog.db import connect
from catalog.graph.client import GraphClient, QueryError


def _client(db, queries_dir="queries"):
    with connect(db) as conn:
        return GraphClient.from_sqlite(conn, queries_dir=queries_dir)


def test_execute_select_returns_bindings(approved_graph):
    client = _client(approved_graph.db)
    rows = client.execute_query(
        "PREFIX kg: <https://knowledge-atlas.local/kg/> "
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
        "SELECT ?label WHERE { ?c a kg:Capability ; rdfs:label ?label }"
    )
    labels = {r["label"] for r in rows}
    assert labels == {"Release Governance", "Release Management"}


def test_execute_ask(approved_graph):
    client = _client(approved_graph.db)
    rows = client.execute_query(
        "PREFIX kg: <https://knowledge-atlas.local/kg/> "
        "ASK { ?s a kg:Decision }"
    )
    assert rows == [{"ask": "True"}]


def test_invalid_sparql_raises_query_error(approved_graph):
    client = _client(approved_graph.db)
    try:
        client.execute_query("SELECT ?x WHERE { this is not sparql }")
    except QueryError:
        pass
    else:  # pragma: no cover - the assertion is the failure path
        raise AssertionError("expected QueryError for malformed SPARQL")


def test_list_and_load_query(approved_graph):
    client = _client(approved_graph.db)
    names = client.list_queries()
    assert "all_capabilities" in names
    assert "evidence_for_object" in names
    text = client.load_query("all_capabilities")
    assert "kg:Capability" in text


def test_load_missing_query_raises(approved_graph):
    client = _client(approved_graph.db)
    try:
        client.load_query("does_not_exist")
    except QueryError as exc:
        assert "does_not_exist" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected QueryError for missing query")


def test_save_then_run_named_query(approved_graph, tmp_path):
    client = _client(approved_graph.db, queries_dir=str(tmp_path / "q"))
    path = client.save_query(
        "decisions_only",
        "PREFIX kg: <https://knowledge-atlas.local/kg/> "
        "SELECT ?d WHERE { ?d a kg:Decision }",
    )
    assert path.exists()
    assert "decisions_only" in client.list_queries()
    rows = client.run_named_query("decisions_only")
    assert len(rows) == 1


def test_remote_client_uses_injected_fetcher():
    captured = {}

    def fake_fetcher(url, sparql):
        captured["url"] = url
        captured["sparql"] = sparql
        return {
            "head": {"vars": ["label"]},
            "results": {"bindings": [{"label": {"type": "literal", "value": "X"}}]},
        }

    from catalog.rdf.config import FusekiConfig

    client = GraphClient.from_fuseki(FusekiConfig(), fetcher=fake_fetcher)
    assert client.is_remote
    rows = client.execute_query("SELECT ?label WHERE { ?s ?p ?label }")
    assert rows == [{"label": "X"}]
    assert captured["url"].endswith("/sparql")
    assert "SELECT" in captured["sparql"]


def test_remote_client_parses_ask():
    client = GraphClient.from_fuseki(
        __import__("catalog.rdf.config", fromlist=["FusekiConfig"]).FusekiConfig(),
        fetcher=lambda url, q: {"boolean": True},
    )
    assert client.execute_query("ASK { ?s ?p ?o }") == [{"ask": "True"}]
