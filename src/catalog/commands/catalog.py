"""File-catalog commands: ``init-db``, ``scan``, ``watch``, ``stats``,
``show-duplicates``."""

from __future__ import annotations

import argparse

from ..db import connect, init_db, latest_scan_run
from ..links import repository as link_repo
from ..scanner import scan
from ..watcher import watch
from ._common import print_stats


def _cmd_init_db(args: argparse.Namespace) -> None:
    init_db(args.db)
    print(f"Initialized {args.db}")


def _cmd_scan(args: argparse.Namespace) -> None:
    stats = scan(args.config, args.db, args.cache)
    print_stats(stats.as_dict())


def _cmd_watch(args: argparse.Namespace) -> None:
    watch(args.config, args.db, args.cache)


def _cmd_stats(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        run = latest_scan_run(conn)
        indexed = conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE scan_status != 'DELETED'"
        ).fetchone()[0]
        links = link_repo.count_links(conn)
    if run is None:
        print("No scans recorded yet. Run: catalog scan")
    else:
        print(f"Last scan: {run['finished_at']}")
        print_stats(dict(run))
    print(f"Indexed artifacts: {indexed}")
    print(f"Links: {links}")


def _cmd_show_duplicates(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = conn.execute(
            "SELECT sha256, COUNT(*) AS count, GROUP_CONCAT(path, '\n') AS paths "
            "FROM artifacts WHERE scan_status != 'DELETED' "
            "GROUP BY sha256 HAVING COUNT(*) > 1"
        ).fetchall()
    for row in rows:
        print(f"{row['sha256']} ({row['count']})\n{row['paths']}\n")


def register(sub: argparse._SubParsersAction) -> None:
    sub.add_parser("init-db").set_defaults(func=_cmd_init_db)
    sub.add_parser("scan").set_defaults(func=_cmd_scan)
    sub.add_parser("watch").set_defaults(func=_cmd_watch)
    sub.add_parser("stats").set_defaults(func=_cmd_stats)
    sub.add_parser("show-duplicates").set_defaults(func=_cmd_show_duplicates)


__all__ = ["register"]
