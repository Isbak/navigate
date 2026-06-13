from __future__ import annotations

import argparse
import logging

from .db import connect, init_db
from .scanner import scan
from .watcher import watch


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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
        print(f"Scanned {scan(args.config, args.db, args.cache)} files")
    elif args.command == "watch":
        watch(args.config, args.db, args.cache)
    elif args.command == "stats":
        init_db(args.db)
        with connect(args.db) as conn:
            artifacts = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        print(f"artifacts={artifacts}\nlinks={links}")
    elif args.command == "show-duplicates":
        init_db(args.db)
        with connect(args.db) as conn:
            rows = conn.execute("SELECT sha256, COUNT(*) AS count, GROUP_CONCAT(path, '\n') AS paths FROM artifacts GROUP BY sha256 HAVING COUNT(*) > 1").fetchall()
        for row in rows:
            print(f"{row['sha256']} ({row['count']})\n{row['paths']}\n")
    elif args.command == "show-links":
        init_db(args.db)
        with connect(args.db) as conn:
            rows = conn.execute("SELECT source_artifact_id,target_url,anchor_text,target_system,target_type FROM links ORDER BY discovered_at DESC").fetchall()
        for row in rows:
            print(f"{row['source_artifact_id']} -> {row['target_url']} [{row['target_system']}/{row['target_type']}] {row['anchor_text'] or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
