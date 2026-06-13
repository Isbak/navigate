from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

from .db import connect, init_db, latest_scan_run
from .extraction import extract_all
from .links import discover_links, load_link_config
from .links import repository as link_repo
from .scanner import scan
from .semantic import analytics as sem_analytics
from .semantic import repository as sem_repo
from .semantic.config import load_llm_config
from .semantic.providers import LLMError, build_provider
from .semantic.service import classify_documents
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
    parser.add_argument("--llm-config", default="config/llm.yml")
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

    classify = sub.add_parser("classify")
    classify.add_argument("--artifact-id", default=None)
    classify.add_argument("--force", action="store_true")

    sub.add_parser("classification-stats")

    summary = sub.add_parser("show-summary")
    summary.add_argument("--artifact-id", required=True)

    for name in ("show-decisions", "show-risks", "show-capabilities", "show-relationships"):
        p = sub.add_parser(name)
        p.add_argument("--min-confidence", type=float, default=0.0)
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


def _cmd_classify(args) -> None:
    config = load_llm_config(args.llm_config)
    try:
        provider = build_provider(config)
    except LLMError as exc:
        print(f"Error: {exc}")
        return
    print(f"Classifying with {config.provider} model {provider.model} ...")
    stats = classify_documents(
        db_path=args.db,
        cache_dir=args.cache,
        provider=provider,
        artifact_id=args.artifact_id,
        force=args.force,
        max_input_chars=config.max_input_chars,
    )
    print("Classification complete:")
    print(f"Documents processed: {_fmt(stats.documents_processed)}")
    print(f"Documents skipped (unchanged): {_fmt(stats.documents_skipped)}")
    print(f"Errors: {_fmt(stats.errors)}")
    print(f"Candidate entities: {_fmt(stats.entities)}")
    print(f"Candidate capabilities: {_fmt(stats.capabilities)}")
    print(f"Candidate decisions: {_fmt(stats.decisions)}")
    print(f"Candidate risks: {_fmt(stats.risks)}")
    print(f"Candidate relationships: {_fmt(stats.relationships)}")
    if stats.by_document_type:
        print("\nDocument types (this run):")
        for dtype, count in sorted(
            stats.by_document_type.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            print(f"  {dtype}: {_fmt(count)}")


def _print_ranked(title: str, rows: list[dict], value_key: str, name_key: str = "name") -> None:
    print(f"\n{title}:")
    if not rows:
        print("  (none)")
        return
    for row in rows:
        print(f"  {_fmt(row[value_key])}  {row[name_key]}")


def _cmd_classification_stats(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        total = sem_repo.count_classifications(conn)
        print(f"Classified documents: {_fmt(total)}")
        if total == 0:
            print("No classifications yet. Run: catalog classify")
            return

        print("\nDocument types:")
        for row in sem_repo.document_type_counts(conn):
            print(f"  {row['key']}: {_fmt(row['count'])}")

        print("\nCandidate objects (knowledge proposed, not facts):")
        for table, label in (
            ("candidate_entities", "entities"),
            ("candidate_capabilities", "capabilities"),
            ("candidate_decisions", "decisions"),
            ("candidate_risks", "risks"),
            ("candidate_relationships", "relationships"),
        ):
            print(f"  {label}: {_fmt(sem_repo.count_rows(conn, table))}")

        _print_ranked("Top domains (by documents)", sem_analytics.top_domains(conn, 10), "documents")
        _print_ranked("Top capabilities (by documents)", sem_analytics.top_capabilities(conn, 10), "documents")
        _print_ranked("Most common technologies", sem_analytics.top_technologies(conn, 10), "documents")
        _print_ranked("Most referenced concepts", sem_analytics.top_concepts(conn, 10), "documents")

        print("\nMost common decision themes:")
        themes = sem_analytics.decision_themes(conn, 10)
        if not themes:
            print("  (none)")
        for t in themes:
            print(f"  {_fmt(t['documents'])} docs  {t['text']}")

        print("\nRisks across multiple documents:")
        risks = [r for r in sem_analytics.risk_themes(conn, 20) if r["documents"] > 1]
        if not risks:
            print("  (none)")
        for r in risks[:10]:
            print(f"  {_fmt(r['documents'])} docs  {r['text']}")

        print("\nConcepts connecting multiple domains:")
        concepts = sem_analytics.concepts_connecting_domains(conn, min_domains=2, limit=10)
        if not concepts:
            print("  (none)")
        for c in concepts:
            print(f"  {c['name']} -> {', '.join(c['domains'])}")


def _cmd_show_summary(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        row = sem_repo.get_classification(conn, args.artifact_id)
    if row is None:
        print(f"No classification for {args.artifact_id}. Run: catalog classify")
        return
    print(f"Artifact: {row['artifact_id']}")
    print(f"Type: {row['document_type']} (confidence {row['type_confidence']:.2f})")
    domains = json.loads(row["domains"] or "[]")
    if domains:
        print("Domains: " + ", ".join(f"{d['domain']} ({d['confidence']:.2f})" for d in domains))
    print(f"Model: {row['model']}    Reviewed: {row['review_status']}")
    print(f"\nShort summary:\n{row['short_summary']}")
    print(f"\nLong summary:\n{row['long_summary']}")


def _cmd_show_decisions(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = sem_repo.decisions(conn, args.min_confidence)
    for row in rows:
        quote = f"  — \"{row['supporting_text']}\"" if row["supporting_text"] else ""
        print(f"[{row['confidence']:.2f}] {row['decision_text']} ({row['artifact_id']}){quote}")
    if not rows:
        print("No candidate decisions.")


def _cmd_show_risks(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = sem_repo.risks(conn, args.min_confidence)
    for row in rows:
        quote = f"  — \"{row['supporting_text']}\"" if row["supporting_text"] else ""
        print(f"[{row['confidence']:.2f}] {row['risk_description']} ({row['artifact_id']}){quote}")
    if not rows:
        print("No candidate risks.")


def _cmd_show_capabilities(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = sem_repo.capabilities(conn, args.min_confidence)
    for row in rows:
        print(f"[{row['confidence']:.2f}] {row['name']} ({row['artifact_id']})")
    if not rows:
        print("No candidate capabilities.")


def _cmd_show_relationships(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = sem_repo.relationships(conn, args.min_confidence)
    for row in rows:
        print(
            f"[{row['confidence']:.2f}] {row['subject']} {row['predicate']} "
            f"{row['object']} ({row['artifact_id']})"
        )
    if not rows:
        print("No candidate relationships.")


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
    elif args.command == "classify":
        _cmd_classify(args)
    elif args.command == "classification-stats":
        _cmd_classification_stats(args)
    elif args.command == "show-summary":
        _cmd_show_summary(args)
    elif args.command == "show-decisions":
        _cmd_show_decisions(args)
    elif args.command == "show-risks":
        _cmd_show_risks(args)
    elif args.command == "show-capabilities":
        _cmd_show_capabilities(args)
    elif args.command == "show-relationships":
        _cmd_show_relationships(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
