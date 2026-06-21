"""Optional Rich-powered terminal explorer for the knowledge graph.

A tiny read-only REPL over the approved graph: search for an object, drill into
it, walk its neighbours, and inspect its evidence - all without leaving the
terminal and without any LLM. It is a thin convenience over the same
:class:`GraphClient` and NetworkX view the one-shot commands use.

Commands inside the explorer::

    search <term>     find objects by label/description
    show <id>         object detail (type, confidence, evidence count)
    neighbors <id>    connected objects grouped by relationship
    evidence <id>     supporting quotes for an object
    help              list commands
    quit / exit       leave

This module is intentionally import-light at module scope so the rest of the
package does not pay for it; Rich is imported lazily inside :func:`run_explorer`.
"""

from __future__ import annotations

from . import network
from .client import GraphClient


def run_explorer(client: GraphClient, *, input_fn=input) -> None:
    """Start the interactive loop. ``input_fn`` is injectable for testing."""

    from rich.console import Console

    console = Console()
    graph = network.build_digraph(client)
    console.print(
        f"[bold]Knowledge Explorer[/bold] - {graph.number_of_nodes()} objects, "
        f"{graph.number_of_edges()} relationships. Type 'help' or 'quit'."
    )

    while True:
        try:
            raw = input_fn("graph> ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        line = raw.strip()
        if not line:
            continue
        command, _, argument = line.partition(" ")
        command = command.lower()
        argument = argument.strip()

        if command in {"quit", "exit", "q"}:
            break
        if command in {"help", "?"}:
            _print_help(console)
        elif command == "search":
            _do_search(console, client, argument)
        elif command == "show":
            _do_show(console, graph, argument)
        elif command in {"neighbors", "neighbours"}:
            _do_neighbors(console, graph, argument)
        elif command == "evidence":
            _do_evidence(console, client, argument)
        else:
            console.print(f"[red]Unknown command: {command}[/red] (try 'help')")


def _print_help(console) -> None:
    console.print(
        "Commands: search <term> | show <id> | neighbors <id> | "
        "evidence <id> | help | quit"
    )


def _do_search(console, client: GraphClient, term: str) -> None:
    if not term:
        console.print("Usage: search <term>")
        return
    from .cli import search_objects  # local import avoids a cycle at import time

    rows = search_objects(client, term)
    if not rows:
        console.print(f"No objects match {term!r}.")
        return
    for row in rows:
        console.print(f"  {row['id']}  [bold]{row['label']}[/bold] ({row['type']})")


def _do_show(console, graph, object_id: str) -> None:
    if object_id not in graph:
        console.print(f"No object with id {object_id!r}.")
        return
    data = graph.nodes[object_id]
    console.print(f"[bold]{data.get('label', object_id)}[/bold] ({data.get('type')})")
    grouped = network.neighbors(graph, object_id)
    total = sum(len(v) for v in grouped.values())
    console.print(f"  relationships: {total}")


def _do_neighbors(console, graph, object_id: str) -> None:
    if object_id not in graph:
        console.print(f"No object with id {object_id!r}.")
        return
    grouped = network.neighbors(graph, object_id)
    if not grouped:
        console.print("  (no neighbours)")
        return
    for predicate, items in sorted(grouped.items()):
        console.print(f"[bold]{predicate}[/bold]")
        for item in items:
            arrow = "->" if item["direction"] == "out" else "<-"
            console.print(f"  {arrow} {item['label']} ({item['type']})")


def _do_evidence(console, client: GraphClient, object_id: str) -> None:
    if not object_id:
        console.print("Usage: evidence <id>")
        return
    from .cli import evidence_for

    quotes = evidence_for(client, object_id)
    if not quotes:
        console.print("  (no evidence)")
        return
    for quote in quotes:
        console.print(f"  {quote['artifact']}: \"{quote['quote']}\"")


__all__ = ["run_explorer"]
