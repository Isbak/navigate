from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path

from .db import connect, init_db, latest_scan_run
from .extraction import extract_all
from .links import discover_links, load_link_config
from .links import repository as link_repo
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


def _fmt(n: int) -> str:
    return f"{n:,}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="catalog")
    parser.add_argument("--db", default="data/catalog.sqlite")
    parser.add_argument("--config", default="config/sources.yml")
    parser.add_argument("--cache", default="cache")
    parser.add_argument("--link-config", default="config/link_patterns.yml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("scan")
    sub.add_parser("watch")
    sub.add_parser("stats")
    sub.add_parser("show-duplicates")
    sub.add_parser("extract")

    discover = sub.add_parser("discover-links")
    discover.add_argument("--artifact-id", default=None)

    sub.add_parser("link-stats")

    show = sub.add_parser("show-links")
    show.add_argument("--artifact-id", default=None)
    show.add_argument("--system", default=None)

    sub.add_parser("show-stale-links")
    sub.add_parser("export-links-csv")
    return parser


def _cmd_stats(args) -> None:
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
        _print_stats(dict(run))
    print(f"Indexed artifacts: {indexed}")
    print(f"Links: {links}")


def _cmd_show_duplicates(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = conn.execute(
            "SELECT sha256, COUNT(*) AS count, GROUP_CONCAT(path, '\n') AS paths "
            "FROM artifacts WHERE scan_status != 'DELETED' "
            "GROUP BY sha256 HAVING COUNT(*) > 1"
        ).fetchall()
    for row in rows:
        print(f"{row['sha256']} ({row['count']})\n{row['paths']}\n")


def _cmd_extract(args) -> None:
    init_db(args.db)
    summary = extract_all(args.db, args.cache)
    print("Extraction complete:")
    print(f"Artifacts processed: {_fmt(summary['artifacts_processed'])}")
    print(f"Links extracted: {_fmt(summary['links_extracted'])}")
    print(f"Errors: {_fmt(summary['errors'])}")


def _cmd_discover_links(args) -> None:
    config = load_link_config(args.link_config)
    stats = discover_links(
        db_path=args.db,
        cache_dir=args.cache,
        config=config,
        artifact_id=args.artifact_id,
    )
    print("Link discovery complete:")
    print(f"Artifacts processed: {_fmt(stats.artifacts_processed)}")
    print(f"Links found: {_fmt(stats.links_found)}")
    print(f"New links: {_fmt(stats.links_new)}")
    print(f"Updated links: {_fmt(stats.links_updated)}")
    print(f"Stale links: {_fmt(stats.links_removed)}")
    print(f"Errors: {_fmt(stats.errors)}")
    if stats.by_system:
        print("\nTarget systems:")
        for system, count in sorted(
            stats.by_system.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            print(f"{system}: {_fmt(count)}")


def _cmd_link_stats(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        total = link_repo.count_links(conn)
        print(f"Total links: {_fmt(total)}")

        for label, column in (
            ("Links by target system", "target_system"),
            ("Links by target type", "target_type"),
            ("Links by link kind", "link_kind"),
        ):
            print(f"\n{label}:")
            for row in link_repo.counts_by(conn, column):
                print(f"  {row['key'] or 'unknown'}: {_fmt(row['count'])}")

        print("\nTop 20 most referenced URLs:")
        for row in link_repo.top_referenced_urls(conn, 20):
            print(f"  {_fmt(row['count'])}  {row['key']}")

        print("\nTop 20 artifacts with most outgoing links:")
        for row in link_repo.top_linking_artifacts(conn, 20):
            print(f"  {_fmt(row['count'])}  {row['key']}")


def _print_link_row(row) -> None:
    anchor = f" \"{row['anchor_text']}\"" if row["anchor_text"] else ""
    print(
        f"{row['source_artifact_id']} -> {row['normalized_url']} "
        f"[{row['target_system']}/{row['target_type']}/{row['link_kind']}] "
        f"({row['status']}){anchor}"
    )


def _cmd_show_links(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        if args.artifact_id:
            rows = link_repo.links_for_artifact(conn, args.artifact_id)
        elif args.system:
            rows = link_repo.links_for_system(conn, args.system)
        else:
            rows = link_repo.all_links(conn)
    for row in rows:
        _print_link_row(row)
    if not rows:
        print("No matching links.")


def _cmd_show_stale_links(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = link_repo.stale_links(conn)
    for row in rows:
        _print_link_row(row)
    if not rows:
        print("No stale links.")


def _cmd_export_links_csv(args) -> None:
    init_db(args.db)
    out_path = Path("exports/links.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "id",
        "source_artifact_id",
        "raw_url",
        "normalized_url",
        "anchor_text",
        "target_system",
        "target_type",
        "link_kind",
        "discovered_at",
        "last_seen_at",
        "status",
    ]
    with connect(args.db) as conn:
        rows = link_repo.all_links(conn)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[col] for col in columns])
    print(f"Exported {_fmt(len(rows))} links to {out_path}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
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
        _cmd_stats(args)
    elif args.command == "show-duplicates":
        _cmd_show_duplicates(args)
    elif args.command == "extract":
        _cmd_extract(args)
    elif args.command == "discover-links":
        _cmd_discover_links(args)
    elif args.command == "link-stats":
        _cmd_link_stats(args)
    elif args.command == "show-links":
        _cmd_show_links(args)
    elif args.command == "show-stale-links":
        _cmd_show_stale_links(args)
    elif args.command == "export-links-csv":
        _cmd_export_links_csv(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
