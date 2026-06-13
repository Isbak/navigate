"""CLI surface for the Knowledge Explorer (``catalog graph ...``).

Wires the SPARQL query layer (``GraphClient``) and the NetworkX exploration
helpers into the ``catalog`` command line, rendering results with Rich. By
default everything runs against an in-memory projection built from SQLite, so no
Fuseki is required; ``--fuseki`` reroutes the SPARQL to a live endpoint instead.

Nothing here is an LLM, GraphRAG, vector, or chat layer - it is pure SPARQL,
graph algorithms, and validation, exactly as the phase requires.
"""

from __future__ import annotations

import argparse

from ..db import connect, init_db
from ..rdf.config import load_jena_config
from ..rdf.namespaces import BASE
from . import network
from .client import GraphClient, QueryError
from .loader import id_to_uri, uri_to_id

_PREFIXES = (
    "PREFIX kg: <https://knowledge-atlas.local/kg/>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
)


def _console():
    from rich.console import Console

    # A fixed width keeps output stable across terminals and in captured tests.
    return Console(width=120, highlight=False)


def _sparql_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _abbrev(value: str | None) -> str:
    """Abbreviate a kg instance URI to its stable id; pass anything else through."""

    if value is None:
        return ""
    if value.startswith(BASE):
        return uri_to_id(value)
    return value


# -- reusable SPARQL helpers (shared with the interactive explorer) -----------

def search_objects(client: GraphClient, term: str) -> list[dict]:
    """Objects whose label or description contains ``term`` (case-insensitive)."""

    needle = _sparql_escape(term.lower())
    query = _PREFIXES + f"""
    SELECT ?s ?type ?label ?comment WHERE {{
        ?s a ?type ; rdfs:label ?label .
        OPTIONAL {{ ?s rdfs:comment ?comment }}
        FILTER(STRSTARTS(STR(?type), "{BASE}"))
        FILTER(?type != kg:Evidence)
        FILTER(CONTAINS(LCASE(STR(?label)), "{needle}")
            || CONTAINS(LCASE(STR(?comment)), "{needle}"))
    }}
    ORDER BY ?label
    """
    rows = client.execute_query(query)
    return [
        {
            "id": uri_to_id(r["s"]),
            "label": r.get("label") or uri_to_id(r["s"]),
            "type": r["type"].rsplit("/", 1)[-1],
            "description": r.get("comment") or "",
        }
        for r in rows
    ]


def object_detail(client: GraphClient, object_id: str) -> dict | None:
    """Type, label, description and confidence for one object, or None."""

    uri = id_to_uri(object_id)
    query = _PREFIXES + f"""
    SELECT ?type ?label ?comment ?confidence WHERE {{
        <{uri}> a ?type .
        FILTER(?type != kg:Evidence)
        OPTIONAL {{ <{uri}> rdfs:label ?label }}
        OPTIONAL {{ <{uri}> rdfs:comment ?comment }}
        OPTIONAL {{ <{uri}> kg:confidence ?confidence }}
    }}
    """
    rows = client.execute_query(query)
    if not rows:
        return None
    row = rows[0]
    return {
        "id": object_id,
        "type": row["type"].rsplit("/", 1)[-1] if row.get("type") else "",
        "label": row.get("label") or object_id,
        "description": row.get("comment") or "",
        "confidence": float(row["confidence"]) if row.get("confidence") else 0.0,
    }


def evidence_for(client: GraphClient, object_id: str) -> list[dict]:
    """Supporting evidence quotes for an object."""

    uri = id_to_uri(object_id)
    query = _PREFIXES + f"""
    SELECT ?artifact ?quote ?confidence WHERE {{
        <{uri}> kg:supportedBy ?e .
        ?e kg:sourceArtifact ?artifact .
        OPTIONAL {{ ?e kg:quote ?quote }}
        OPTIONAL {{ ?e kg:confidence ?confidence }}
    }}
    """
    return [
        {
            "artifact": r.get("artifact") or "",
            "quote": r.get("quote") or "",
            "confidence": float(r["confidence"]) if r.get("confidence") else 0.0,
        }
        for r in client.execute_query(query)
    ]


def evidence_count(client: GraphClient, object_id: str) -> int:
    uri = id_to_uri(object_id)
    query = _PREFIXES + f"""
    SELECT (COUNT(?e) AS ?n) WHERE {{ <{uri}> kg:supportedBy ?e }}
    """
    rows = client.execute_query(query)
    return int(rows[0]["n"]) if rows and rows[0].get("n") else 0


def _resolve_id(graph, term: str):
    """Resolve a user argument to a node id. Returns (id, candidates)."""

    if term in graph:
        return term, []
    lowered = term.lower()
    matches = [
        n for n, d in graph.nodes(data=True)
        if lowered in d.get("label", "").lower() or lowered in n.lower()
    ]
    if len(matches) == 1:
        return matches[0], []
    return None, matches


# -- client construction ------------------------------------------------------

def _make_client(args) -> GraphClient:
    if getattr(args, "fuseki", False):
        config = load_jena_config(args.jena_config)
        return GraphClient.from_fuseki(config, queries_dir=args.queries_dir)
    init_db(args.db)
    with connect(args.db) as conn:
        return GraphClient.from_sqlite(conn, queries_dir=args.queries_dir)


# -- command handlers ---------------------------------------------------------

def _cmd_query(args) -> None:
    console = _console()
    client = _make_client(args)
    if not args.query_name:
        names = client.list_queries()
        console.print("[bold]Saved queries[/bold] (catalog graph query <name>):")
        for name in names:
            console.print(f"  {name}")
        if not names:
            console.print("  (none)")
        return
    try:
        rows = client.run_named_query(args.query_name)
    except QueryError as exc:
        console.print(f"[red]{exc}[/red]")
        return
    _render_bindings(console, rows, title=args.query_name)


def _render_bindings(console, rows: list[dict], *, title: str) -> None:
    from rich.table import Table

    if not rows:
        console.print(f"{title}: no results.")
        return
    columns = list(rows[0].keys())
    table = Table(title=title, show_lines=False)
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*[_abbrev(row.get(c)) for c in columns])
    console.print(table)
    console.print(f"{len(rows)} row(s).")


def _cmd_search(args) -> None:
    console = _console()
    client = _make_client(args)
    rows = search_objects(client, args.term)
    if not rows:
        console.print(f"No objects match {args.term!r}.")
        return
    graph = network.build_digraph(client)
    from rich.table import Table

    table = Table(title=f"Search: {args.term}")
    table.add_column("id", overflow="fold")
    table.add_column("name")
    table.add_column("type")
    table.add_column("relationships", justify="right")
    for row in rows:
        rel_count = graph.degree(row["id"]) if row["id"] in graph else 0
        table.add_row(row["id"], row["label"], row["type"], str(rel_count))
    console.print(table)
    console.print(f"{len(rows)} match(es).")


def _cmd_show(args) -> None:
    console = _console()
    client = _make_client(args)
    graph = network.build_digraph(client)
    object_id, candidates = _resolve_id(graph, args.object)
    if object_id is None:
        _print_unresolved(console, args.object, candidates)
        return

    detail = object_detail(client, object_id)
    if detail is None:
        console.print(f"No object with id {object_id!r}.")
        return
    grouped = network.neighbors(graph, object_id)
    rel_count = sum(len(v) for v in grouped.values())
    ev_count = evidence_count(client, object_id)

    console.print(f"[bold]{detail['label']}[/bold]")
    console.print(f"\nId: {object_id}")
    console.print(f"Type: {detail['type']}")
    console.print(f"Confidence: {detail['confidence']:.2f}")
    if detail["description"]:
        console.print(f"Description: {detail['description']}")
    console.print(f"Relationship count: {rel_count}")
    console.print(f"Evidence count: {ev_count}")

    console.print("\n[bold]Connected objects[/bold]:")
    if not grouped:
        console.print("  (none)")
    for predicate, items in sorted(grouped.items()):
        console.print(f"  {predicate}:")
        for item in items:
            arrow = "->" if item["direction"] == "out" else "<-"
            console.print(f"    {arrow} {item['label']} ({item['type']})")


def _cmd_path(args) -> None:
    console = _console()
    client = _make_client(args)
    graph = network.build_digraph(client)
    src, src_cands = _resolve_id(graph, args.object1)
    tgt, tgt_cands = _resolve_id(graph, args.object2)
    if src is None:
        _print_unresolved(console, args.object1, src_cands)
        return
    if tgt is None:
        _print_unresolved(console, args.object2, tgt_cands)
        return

    hops = network.shortest_path(graph, src, tgt)
    if hops is None:
        console.print(
            f"No path between {network.label_of(graph, src)} and "
            f"{network.label_of(graph, tgt)}."
        )
        return
    if not hops:
        console.print(network.label_of(graph, src))
        return
    console.print(f"[bold]Shortest path[/bold] ({len(hops)} hop(s)):\n")
    console.print(network.label_of(graph, hops[0]["from"]))
    for hop in hops:
        console.print(f"    {hop['predicate']}")
        console.print(network.label_of(graph, hop["to"]))


def _cmd_neighbors(args) -> None:
    console = _console()
    client = _make_client(args)
    graph = network.build_digraph(client)
    object_id, candidates = _resolve_id(graph, args.object)
    if object_id is None:
        _print_unresolved(console, args.object, candidates)
        return
    grouped = network.neighbors(graph, object_id)
    console.print(f"[bold]Neighbors of {network.label_of(graph, object_id)}[/bold]:")
    if not grouped:
        console.print("  (none)")
        return
    for predicate, items in sorted(grouped.items()):
        console.print(f"\n[bold]{predicate}[/bold]:")
        for item in items:
            arrow = "->" if item["direction"] == "out" else "<-"
            console.print(f"  {arrow} {item['label']} ({item['type']})")


def _cmd_impact(args) -> None:
    console = _console()
    client = _make_client(args)
    graph = network.build_digraph(client)
    object_id, candidates = _resolve_id(graph, args.object)
    if object_id is None:
        _print_unresolved(console, args.object, candidates)
        return
    grouped = network.impact(graph, object_id)
    total = sum(len(v) for v in grouped.values())
    console.print(
        f"[bold]Impact of {network.label_of(graph, object_id)}[/bold] "
        f"- {total} directly connected object(s):"
    )
    if not grouped:
        console.print("  (none)")
        return
    for object_type in sorted(grouped):
        items = grouped[object_type]
        console.print(f"\n[bold]{object_type}[/bold] ({len(items)}):")
        for item in items:
            console.print(f"  {item['label']}")


def _cmd_health(args) -> None:
    from .health import knowledge_health

    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        report = knowledge_health(conn)

    console.print("[bold]Knowledge health[/bold]")
    console.print(
        f"Approved objects: {report['object_count']}    "
        f"relationships: {report['relationship_count']}"
    )

    def _section(title: str, items: list, render) -> None:
        console.print(f"\n[bold]{title}[/bold] ({len(items)}):")
        if not items:
            console.print("  (none)")
        for item in items[:15]:
            console.print(f"  {render(item)}")

    _section(
        "Objects without relationships",
        report["objects_without_relationships"],
        lambda o: f"{o['name']} ({o['type']})",
    )
    _section(
        "Objects without evidence",
        report["objects_without_evidence"],
        lambda o: f"{o['name']} ({o['type']})",
    )
    _section(
        "Relationships without evidence",
        report["relationships_without_evidence"],
        lambda r: f"{r['source']} {r['predicate']} {r['target']}",
    )
    _section(
        "Low-confidence objects",
        report["low_confidence_objects"],
        lambda o: f"[{o['confidence']:.2f}] {o['name']} ({o['type']})",
    )
    _section(
        "Duplicate candidates",
        report["duplicate_candidates"],
        lambda d: f"[{d['similarity']:.2f}] {d['left_name']} <-> {d['right_name']}",
    )

    subgraphs = report["disconnected_subgraphs"]
    console.print(f"\n[bold]Disconnected subgraphs[/bold] ({len(subgraphs)}):")
    if len(subgraphs) <= 1:
        console.print("  (graph is connected)" if subgraphs else "  (none)")
    for index, component in enumerate(subgraphs, start=1):
        labels = ", ".join(m["label"] for m in component["members"][:5])
        more = "" if component["size"] <= 5 else f", +{component['size'] - 5} more"
        console.print(f"  #{index} ({component['size']}): {labels}{more}")

    _section(
        "Most connected nodes",
        report["most_connected"],
        lambda n: f"{n['degree']} links  {n['label']}",
    )


def _cmd_domains(args) -> None:
    from .domains import analyze_domains

    console = _console()
    client = _make_client(args)
    domains = analyze_domains(client)
    console.print("[bold]Knowledge domains[/bold]")
    if not domains:
        console.print("  (none)")
        return
    for domain in domains:
        console.print(
            f"\n[bold]{domain['domain']}[/bold]: "
            f"{domain['object_count']} objects, "
            f"{domain['relationship_count']} relationships"
        )
        central = ", ".join(
            f"{c['label']} ({c['degree']})" for c in domain["most_central"]
        )
        if central:
            console.print(f"  central: {central}")


def _cmd_metrics(args) -> None:
    from .export import export_visualization

    console = _console()
    client = _make_client(args)
    graph = network.build_digraph(client)
    metrics = network.compute_metrics(graph)

    console.print("[bold]Network analysis[/bold]")
    console.print(
        f"Objects: {metrics['node_count']}    "
        f"Relationships: {metrics['edge_count']}    "
        f"Density: {metrics['density']}"
    )
    console.print(
        f"Connected components: {metrics['connected_components']}    "
        f"Clusters: {metrics['clusters']}"
    )
    console.print("\n[bold]Most central knowledge objects[/bold]:")
    if not metrics["top"]:
        console.print("  (none)")
    for node in metrics["top"]:
        console.print(
            f"  {node['label']} ({node['type']})  "
            f"Degree: {node['degree']}  Betweenness: {node['betweenness']}"
        )

    paths = export_visualization(graph, args.out)
    console.print("\nVisualization data written:")
    for name in ("nodes", "edges", "metrics"):
        console.print(f"  {paths[name]}")


def _cmd_export(args, fmt: str) -> None:
    from .export import export_gexf, export_graphml, export_json

    console = _console()
    client = _make_client(args)
    graph = network.build_digraph(client)
    out_dir = args.out
    writers = {
        "gexf": (export_gexf, "knowledge.gexf"),
        "graphml": (export_graphml, "knowledge.graphml"),
        "json": (export_json, "graph.json"),
    }
    writer, filename = writers[fmt]
    path = writer(graph, f"{out_dir}/{filename}")
    console.print(
        f"Exported {graph.number_of_nodes()} nodes / "
        f"{graph.number_of_edges()} edges to {path}"
    )


def _cmd_explore(args) -> None:
    from .explorer import run_explorer

    client = _make_client(args)
    run_explorer(client)


def _print_unresolved(console, term: str, candidates: list[str]) -> None:
    if candidates:
        console.print(f"{term!r} is ambiguous. Did you mean:")
        for candidate in candidates[:10]:
            console.print(f"  {candidate}")
    else:
        console.print(f"No object matching {term!r}.")


# -- parser wiring ------------------------------------------------------------

def add_graph_parser(sub: argparse._SubParsersAction) -> None:
    """Register the ``graph`` command group on the top-level subparsers."""

    graph = sub.add_parser("graph", help="explore the approved knowledge graph")
    graph.add_argument(
        "--fuseki",
        action="store_true",
        help="run SPARQL against the live Fuseki endpoint instead of SQLite",
    )
    graph.add_argument("--queries-dir", default="queries")
    graph.add_argument(
        "--out", default="exports/graph", help="output directory for exports"
    )
    gsub = graph.add_subparsers(dest="graph_command", required=True)

    q = gsub.add_parser("query", help="run a saved SPARQL query (or list them)")
    q.add_argument("query_name", nargs="?")

    s = gsub.add_parser("search", help="search objects by label/description")
    s.add_argument("term")

    sh = gsub.add_parser("show", help="object detail view")
    sh.add_argument("object")

    p = gsub.add_parser("path", help="shortest path between two objects")
    p.add_argument("object1")
    p.add_argument("object2")

    nb = gsub.add_parser("neighbors", help="connected objects by relationship")
    nb.add_argument("object")

    im = gsub.add_parser("impact", help="what may be affected by an object")
    im.add_argument("object")

    gsub.add_parser("health", help="knowledge validation report")
    gsub.add_parser("domains", help="knowledge domain analysis")
    gsub.add_parser("metrics", help="network analysis + visualization data")
    gsub.add_parser("export-gexf", help="export GEXF (Gephi)")
    gsub.add_parser("export-graphml", help="export GraphML (Neo4j/yEd/Cytoscape)")
    gsub.add_parser("export-json", help="export node-link JSON")
    gsub.add_parser("explore", help="interactive terminal explorer")


def run_graph(args) -> None:
    """Dispatch a parsed ``graph`` subcommand."""

    handlers = {
        "query": _cmd_query,
        "search": _cmd_search,
        "show": _cmd_show,
        "path": _cmd_path,
        "neighbors": _cmd_neighbors,
        "impact": _cmd_impact,
        "health": _cmd_health,
        "domains": _cmd_domains,
        "metrics": _cmd_metrics,
        "explore": _cmd_explore,
    }
    command = args.graph_command
    if command in handlers:
        handlers[command](args)
    elif command == "export-gexf":
        _cmd_export(args, "gexf")
    elif command == "export-graphml":
        _cmd_export(args, "graphml")
    elif command == "export-json":
        _cmd_export(args, "json")


__all__ = ["add_graph_parser", "run_graph", "search_objects", "evidence_for"]
