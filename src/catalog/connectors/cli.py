"""Sub-CLI for the connector command group (``catalog connector ...``).

Typical workflow::

    catalog connector list
    catalog connector sync                # sync all enabled connectors
    catalog connector sync eng-repos      # sync one connector by name
    catalog connector sync --dry-run      # preview what would be downloaded
    catalog connector status              # per-connector artifact counts
"""

from __future__ import annotations

import argparse

from ..db import connect, init_db
from . import build_connector
from .base import ConnectorError
from .config import load_connectors_config
from .sync import ConnectorSync


def _console():
    from rich.console import Console
    return Console(width=120, highlight=False)


def _cmd_list(args: argparse.Namespace) -> None:
    console = _console()
    cfg = load_connectors_config(getattr(args, "connector_config", "config/connectors.yml"))
    if not cfg.connectors:
        console.print("No connectors configured. Create config/connectors.yml to get started.")
        return
    console.print(f"[bold]Configured connectors[/bold]  (cache: {cfg.cache_dir})")
    for entry in cfg.connectors:
        state = "[green]enabled[/green]" if entry.enabled else "[dim]disabled[/dim]"
        console.print(f"  {entry.name} ({entry.type}) — {state}")


def _cmd_sync(args: argparse.Namespace) -> None:
    console = _console()
    cfg = load_connectors_config(getattr(args, "connector_config", "config/connectors.yml"))
    if not cfg.connectors:
        console.print("No connectors configured. Create config/connectors.yml to get started.")
        return

    target: str | None = getattr(args, "connector_name", None)
    entries = [e for e in cfg.connectors if e.enabled]
    if target:
        entries = [e for e in entries if e.name == target]
        if not entries:
            console.print(f"[red]No enabled connector named {target!r}[/red]")
            return

    dry_run: bool = getattr(args, "dry_run", False)
    syncer = ConnectorSync(args.db, cfg.cache_dir)

    for entry in entries:
        console.print(f"[bold]Syncing {entry.name}[/bold] ({entry.type})")
        try:
            connector = build_connector(entry)
        except ConnectorError as exc:
            console.print(f"  [red]Configuration error: {exc}[/red]")
            continue
        stats = syncer.sync(connector, dry_run=dry_run)
        d = stats.as_dict()
        console.print(
            f"  New: {d['new_files']}  "
            f"Changed: {d['changed_files']}  "
            f"Unchanged: {d['unchanged_files']}  "
            f"Deleted: {d['deleted_files']}  "
            f"Errors: {d['errors']}"
        )
        if not dry_run and (d["new_files"] or d["changed_files"]):
            console.print(
                "  Run [bold]catalog extract[/bold] then "
                "[bold]catalog classify[/bold] to process new content."
            )


def _cmd_status(args: argparse.Namespace) -> None:
    console = _console()
    cfg = load_connectors_config(getattr(args, "connector_config", "config/connectors.yml"))
    if not cfg.connectors:
        console.print("No connectors configured.")
        return

    init_db(args.db)
    names = [e.name for e in cfg.connectors]
    placeholders = ",".join("?" * len(names))

    with connect(args.db) as conn:
        artifact_rows = conn.execute(
            f"SELECT source_system, scan_status, COUNT(*) as cnt FROM artifacts "
            f"WHERE source_system IN ({placeholders}) "
            f"GROUP BY source_system, scan_status",
            names,
        ).fetchall()
        map_rows = conn.execute(
            f"SELECT connector_name, COUNT(*) as cnt, MAX(synced_at) as last_sync "
            f"FROM connector_file_map WHERE connector_name IN ({placeholders}) "
            f"GROUP BY connector_name",
            names,
        ).fetchall()

    last_sync: dict[str, str] = {row[0]: str(row[2] or "never") for row in map_rows}
    by_connector: dict[str, dict[str, int]] = {}
    for row in artifact_rows:
        src = str(row["source_system"])
        status = str(row["scan_status"])
        by_connector.setdefault(src, {})[status] = int(row["cnt"])

    console.print("[bold]Connector status[/bold]")
    for entry in cfg.connectors:
        counts = by_connector.get(entry.name, {})
        total = sum(v for k, v in counts.items() if k != "DELETED")
        last = last_sync.get(entry.name, "never")
        label = "[green]" if entry.enabled else "[dim]"
        console.print(
            f"  {label}{entry.name}[/] ({entry.type}) — "
            f"{total} files, last sync: {last}"
        )


def add_connector_parser(sub: argparse._SubParsersAction) -> None:
    """Register the ``connector`` command group on the top-level subparsers."""

    p = sub.add_parser("connector", help="sync content from external cloud services")
    csub = p.add_subparsers(dest="connector_command", required=True)

    csub.add_parser("list", help="list configured connectors and their status")

    sync_p = csub.add_parser("sync", help="download new or changed content from connectors")
    sync_p.add_argument(
        "connector_name", nargs="?",
        help="connector name to sync (omit to sync all enabled connectors)",
    )
    sync_p.add_argument(
        "--dry-run", action="store_true",
        help="show what would be downloaded without making changes",
    )

    csub.add_parser("status", help="show per-connector artifact counts")


def run_connector(args: argparse.Namespace) -> None:
    """Dispatch a parsed ``connector`` subcommand."""

    handlers = {
        "list": _cmd_list,
        "sync": _cmd_sync,
        "status": _cmd_status,
    }
    command: str = getattr(args, "connector_command", "")
    handler = handlers.get(command)
    if handler is not None:
        handler(args)


__all__ = ["add_connector_parser", "run_connector"]
