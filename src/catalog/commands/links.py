"""Link discovery and reporting commands: ``extract``, ``discover-links``,
``link-stats``, ``show-links``, ``show-stale-links``, ``export-links-csv``."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from ..config import load_performance_config, resolve_workers
from ..db import connect, init_db
from ..extraction import extract_all
from ..extractors.config import VALID_MODES, load_extraction_config
from ..links import discover_links, load_link_config
from ..links import repository as link_repo
from ._common import fmt


def _cmd_extract(args: argparse.Namespace) -> None:
    init_db(args.db)
    mode = args.mode or load_extraction_config(args.extract_config).mode
    perf = load_performance_config(args.performance_config)
    workers = resolve_workers(args.workers, perf.extract_workers)
    summary = extract_all(
        args.db,
        args.cache,
        mode=mode,
        artifact_ids=args.artifact_id,
        path_glob=args.path_glob,
        workers=workers,
    )
    print(f"Extraction complete (mode: {mode}):")
    print(f"Artifacts processed: {fmt(summary['artifacts_processed'])}")
    print(f"Links extracted: {fmt(summary['links_extracted'])}")
    print(f"Errors: {fmt(summary['errors'])}")


def _cmd_discover_links(args: argparse.Namespace) -> None:
    config = load_link_config(args.link_config)
    perf = load_performance_config(args.performance_config)
    workers = resolve_workers(args.workers, perf.link_workers)
    stats = discover_links(
        db_path=args.db,
        cache_dir=args.cache,
        config=config,
        artifact_id=args.artifact_id,
        workers=workers,
    )
    print("Link discovery complete:")
    print(f"Artifacts processed: {fmt(stats.artifacts_processed)}")
    print(f"Links found: {fmt(stats.links_found)}")
    print(f"New links: {fmt(stats.links_new)}")
    print(f"Updated links: {fmt(stats.links_updated)}")
    print(f"Stale links: {fmt(stats.links_removed)}")
    print(f"Errors: {fmt(stats.errors)}")
    if stats.by_system:
        print("\nTarget systems:")
        for system, count in sorted(stats.by_system.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"{system}: {fmt(count)}")


def _cmd_link_stats(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        total = link_repo.count_links(conn)
        print(f"Total links: {fmt(total)}")

        for label, column in (
            ("Links by target system", "target_system"),
            ("Links by target type", "target_type"),
            ("Links by link kind", "link_kind"),
        ):
            print(f"\n{label}:")
            for row in link_repo.counts_by(conn, column):
                print(f"  {row['key'] or 'unknown'}: {fmt(row['count'])}")

        print("\nTop 20 most referenced URLs:")
        for row in link_repo.top_referenced_urls(conn, 20):
            print(f"  {fmt(row['count'])}  {row['key']}")

        print("\nTop 20 artifacts with most outgoing links:")
        for row in link_repo.top_linking_artifacts(conn, 20):
            print(f"  {fmt(row['count'])}  {row['key']}")


def _print_link_row(row: sqlite3.Row) -> None:
    anchor = f' "{row["anchor_text"]}"' if row["anchor_text"] else ""
    print(
        f"{row['source_artifact_id']} -> {row['normalized_url']} "
        f"[{row['target_system']}/{row['target_type']}/{row['link_kind']}] "
        f"({row['status']}){anchor}"
    )


def _cmd_show_links(args: argparse.Namespace) -> None:
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


def _cmd_show_stale_links(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = link_repo.stale_links(conn)
    for row in rows:
        _print_link_row(row)
    if not rows:
        print("No stale links.")


def _cmd_export_links_csv(args: argparse.Namespace) -> None:
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
    print(f"Exported {fmt(len(rows))} links to {out_path}")


def register(sub: argparse._SubParsersAction) -> None:
    extract = sub.add_parser("extract")
    extract.add_argument(
        "--mode",
        choices=VALID_MODES,
        default=None,
        help="extraction mode (default from config/extraction.yml)",
    )
    extract.add_argument(
        "--artifact-id",
        action="append",
        default=None,
        help="re-extract only this artifact id (repeatable)",
    )
    extract.add_argument(
        "--path-glob",
        default=None,
        help="re-extract only artifacts whose path matches this glob, "
        "e.g. '*.pdf' or '**/eurocode*'",
    )
    extract.add_argument(
        "--workers",
        type=int,
        default=None,
        help="parallel extraction workers (default from config/performance.yml; 0 = one per CPU)",
    )
    extract.set_defaults(func=_cmd_extract)

    discover = sub.add_parser("discover-links")
    discover.add_argument("--artifact-id", default=None)
    discover.add_argument(
        "--workers",
        type=int,
        default=None,
        help="parallel link-resolution workers (default from "
        "config/performance.yml; 0 = one per CPU)",
    )
    discover.set_defaults(func=_cmd_discover_links)

    sub.add_parser("link-stats").set_defaults(func=_cmd_link_stats)

    show = sub.add_parser("show-links")
    show.add_argument("--artifact-id", default=None)
    show.add_argument("--system", default=None)
    show.set_defaults(func=_cmd_show_links)

    sub.add_parser("show-stale-links").set_defaults(func=_cmd_show_stale_links)
    sub.add_parser("export-links-csv").set_defaults(func=_cmd_export_links_csv)


__all__ = ["register"]
