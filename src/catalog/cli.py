from __future__ import annotations

import argparse
import logging

from .db import connect, init_db, latest_scan_run
from .scanner import scan
from .watcher import watch


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _print_stats(stats: dict) -> None:
    print(f"Files scanned: {stats['files_scanned']}")
    print(f"New files: {stats['new_files']}")
    print(f"Modified files: {stats['changed_files']}")
    print(f"Deleted files: {stats['deleted_files']}")
    print(f"Duplicates: {stats['duplicate_files']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="catalog")
    parser.add_argument("--db", default="data/catalog.sqlite")
    parser.add_argument("--config", default="config/sources.yml")
    parser.add_argument("--cache", default="cache")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("scan")
    sub.add_parser("watch")
    sub.add_parser("stats")
    sub.add_parser("show-duplicates")
    sub.add_parser("show-links")
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.command == "init-db":
        init_db(args.db)
        print(f"Initialized {args.db}")
    elif args.command == "scan":
        stats = scan(args.config, args.db, args.cache)
        _print_stats(stats.as_dict())
    elif args.command == "watch":
        watch(args.config, args.db, args.cache)
    elif args.command == "stats":
        init_db(args.db)
        with connect(args.db) as conn:
            run = latest_scan_run(conn)
            indexed = conn.execute(
                "SELECT COUNT(*) FROM artifacts WHERE scan_status != 'DELETED'"
            ).fetchone()[0]
            links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        if run is None:
            print("No scans recorded yet. Run: catalog scan")
        else:
            print(f"Last scan: {run['finished_at']}")
            _print_stats(dict(run))
        print(f"Indexed artifacts: {indexed}")
        print(f"Links: {links}")
    elif args.command == "show-duplicates":
        init_db(args.db)
        with connect(args.db) as conn:
            rows = conn.execute(
                "SELECT sha256, COUNT(*) AS count, GROUP_CONCAT(path, '\n') AS paths "
                "FROM artifacts WHERE scan_status != 'DELETED' "
                "GROUP BY sha256 HAVING COUNT(*) > 1"
            ).fetchall()
        for row in rows:
            print(f"{row['sha256']} ({row['count']})\n{row['paths']}\n")
    elif args.command == "show-links":
        init_db(args.db)
        with connect(args.db) as conn:
            rows = conn.execute(
                "SELECT source_path,target_url,anchor_text,target_system,target_type "
                "FROM links ORDER BY discovered_at DESC"
            ).fetchall()
        for row in rows:
            print(
                f"{row['source_path']} -> {row['target_url']} "
                f"[{row['target_system']}/{row['target_type']}] {row['anchor_text'] or ''}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
