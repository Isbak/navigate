from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

from .db import connect, init_db, latest_scan_run
from .extraction import extract_all
from .governance import service as gov_service
from .governance.cli import add_governance_parser, run_governance
from .graph.cli import add_graph_parser, run_graph
from .graphrag.cli import add_graphrag_parsers, run_graphrag
from .links import discover_links, load_link_config
from .links import repository as link_repo
from .scanner import scan
from .knowledge import analytics as know_analytics
from .knowledge import repository as know_repo
from .knowledge.export import export_graph_json
from .knowledge.models import ReviewState
from .knowledge.prompts import make_merge_judge
from .knowledge.service import (
    approve_relationships_by_confidence,
    consolidate,
    review_object,
    review_relationship,
)
from .rdf.config import load_jena_config
from .rdf.export import DEFAULT_OUT_DIR as RDF_OUT_DIR
from .rdf.export import FORMATS as RDF_FORMATS
from .rdf.export import export_rdf, rdf_stats, validate_rdf
from .rdf.fuseki import FusekiError, clear_dataset, fuseki_load
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
    parser.add_argument("--jena-config", default="config/jena.yml")
    parser.add_argument("--governance-config", default="config/governance.yml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")

    api = sub.add_parser("api", help="run the local REST API server")
    api.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    api.add_argument("--port", type=int, default=None, help="bind port (default 8000)")
    api.add_argument("--reload", dest="reload", action="store_true", help="enable auto-reload")
    api.add_argument("--no-reload", dest="reload", action="store_false", help="disable auto-reload")
    api.add_argument("--api-config", default="config/api.yml")
    api.set_defaults(reload=None)

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

    # -- knowledge consolidation layer (Prompt #6) --
    cons = sub.add_parser("consolidate")
    cons.add_argument("--force", action="store_true")
    cons.add_argument(
        "--use-llm",
        action="store_true",
        help="consult the configured LLM for borderline merge suggestions",
    )

    sub.add_parser("knowledge-stats")

    growth = sub.add_parser(
        "knowledge-growth", help="knowledge-growth trend over time"
    )
    growth.add_argument(
        "--interval", choices=("day", "week", "month"), default="month"
    )
    growth.add_argument("--limit", type=int, default=12)

    show_obj = sub.add_parser("show-object")
    show_obj.add_argument("object_id")

    search_k = sub.add_parser("search-knowledge")
    search_k.add_argument("query")

    sub.add_parser("review-candidates")

    approve = sub.add_parser("approve-object")
    approve.add_argument("object_id")

    reject = sub.add_parser("reject-object")
    reject.add_argument("object_id")

    approve_rel = sub.add_parser("approve-relationship")
    approve_rel.add_argument("relationship_id", type=int)

    approve_interval = sub.add_parser("approve-confidence-interval")
    approve_interval.add_argument("--min-confidence", type=float, required=True)
    approve_interval.add_argument("--max-confidence", type=float, required=True)
    approve_interval.add_argument(
        "--target", choices=("objects", "relationships", "all"), default="all"
    )
    approve_interval.add_argument(
        "--include-reviewed",
        action="store_true",
        help="also approve rows that are already in a REVIEWED state",
    )
    approve_interval.add_argument("--note", default="")

    reject_rel = sub.add_parser("reject-relationship")
    reject_rel.add_argument("relationship_id", type=int)

    sub.add_parser("export-graph-json")

    # -- RDF export and Jena integration (Prompt #7) --
    rdf_export = sub.add_parser("rdf-export")
    rdf_export.add_argument("--out", default=RDF_OUT_DIR)
    rdf_export.add_argument(
        "--format", default="turtle", choices=sorted(RDF_FORMATS)
    )

    rdf_validate = sub.add_parser("rdf-validate")
    rdf_validate.add_argument("--out", default=RDF_OUT_DIR)

    sub.add_parser("rdf-stats")

    fuseki = sub.add_parser("fuseki-load")
    fuseki.add_argument("--out", default=RDF_OUT_DIR)

    sub.add_parser("fuseki-clear")

    # -- knowledge explorer and SPARQL query layer (Prompt #8) --
    add_graph_parser(sub)

    # -- GraphRAG knowledge assistant (Prompt #9) --
    add_graphrag_parsers(sub)

    # -- knowledge governance and continuous operations (Prompt #10) --
    add_governance_parser(sub)

    # -- pipeline benchmark suite --
    bench = sub.add_parser(
        "benchmark", help="run the scan/extract/classify/consolidate/ask benchmark suite"
    )
    bench.add_argument(
        "--stages", default="all",
        help="comma-separated subset of scan,extract,classify,consolidate,ask",
    )
    bench.add_argument(
        "--provider", default="stub",
        help="stub (deterministic) or a real provider: claude/openai/ollama",
    )
    bench.add_argument("--out", default=None, help="write the JSON report to this path")
    bench.add_argument("--format", choices=["table", "json", "both"], default="table")
    bench.add_argument("--thresholds", default=None, help="path to a thresholds JSON")
    bench.add_argument(
        "--check", action="store_true",
        help="exit non-zero if any reported stage fails its quality gate",
    )
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

    def _show_progress(completed: int, total: int, artifact_id: str) -> None:
        percent = round((completed / total) * 100) if total else 100
        print(
            f"Classification progress: {percent}% complete "
            f"({completed}/{total}) {artifact_id}"
        )

    stats = classify_documents(
        db_path=args.db,
        cache_dir=args.cache,
        provider=provider,
        artifact_id=args.artifact_id,
        force=args.force,
        max_input_chars=config.max_input_chars,
        progress_callback=_show_progress,
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


def _cmd_consolidate(args) -> None:
    merge_judge = None
    if args.use_llm:
        config = load_llm_config(args.llm_config)
        try:
            provider = build_provider(config)
        except LLMError as exc:
            print(f"Error: {exc}")
            return
        print(f"Using {config.provider} model {provider.model} for merge suggestions ...")
        merge_judge = make_merge_judge(provider)

    print("Consolidating knowledge objects ...")
    stats = consolidate(args.db, force=args.force, merge_judge=merge_judge)
    print("Consolidation complete:")
    print(f"Mentions gathered: {_fmt(stats.mentions_gathered)}")
    print(f"Knowledge objects: {_fmt(stats.objects_created)}")
    print(f"Mentions linked: {_fmt(stats.mentions_linked)}")
    print(f"Evidence rows: {_fmt(stats.evidence_created)}")
    print(f"Relationships: {_fmt(stats.relationships_created)}")
    print(f"Relationships unresolved: {_fmt(stats.relationships_unresolved)}")
    if not args.force:
        print(f"Review statuses preserved: {_fmt(stats.statuses_preserved)}")
    if stats.by_object_type:
        print("\nObjects by type:")
        for otype, count in sorted(
            stats.by_object_type.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            print(f"  {otype}: {_fmt(count)}")


def _cmd_knowledge_stats(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        total = know_repo.count_objects(conn)
        print(f"Knowledge objects: {_fmt(total)}")
        if total == 0:
            print("No knowledge objects yet. Run: catalog consolidate")
            return

        print(f"Mentions: {_fmt(know_repo.count_table(conn, 'knowledge_mentions'))}")
        print(f"Evidence: {_fmt(know_repo.count_table(conn, 'knowledge_evidence'))}")
        print(
            f"Relationships: {_fmt(know_repo.count_table(conn, 'knowledge_relationships'))}"
        )

        print("\nObjects by type:")
        for row in know_repo.object_type_counts(conn):
            print(f"  {row['key']}: {_fmt(row['count'])}")

        print("\nObjects by review status:")
        for status in (s.value for s in ReviewState):
            count = len(know_repo.objects_by_status(conn, status))
            print(f"  {status}: {_fmt(count)}")

        for label, otype in (
            ("Top capabilities", "Capability"),
            ("Top concepts", "Concept"),
            ("Top technologies", "Technology"),
        ):
            print(f"\n{label} (by documents):")
            rows = know_analytics.top_by_type(conn, otype, 10)
            if not rows:
                print("  (none)")
            for r in rows:
                print(f"  {_fmt(r['documents'])} docs  [{r['confidence']:.2f}]  {r['name']}")

        print("\nMost connected objects:")
        connected = know_analytics.most_connected(conn, 10)
        if not connected:
            print("  (none)")
        for r in connected:
            print(f"  {_fmt(r['degree'])} links  {r['name']} ({r['object_type']})")

        print("\nMost mentioned objects:")
        for r in know_analytics.most_mentioned(conn, 10):
            print(f"  {_fmt(r['documents'])} docs  {r['name']} ({r['object_type']})")

        print("\nObjects with conflicting evidence:")
        conflicts = know_analytics.conflicting_evidence(conn, 10)
        if not conflicts:
            print("  (none)")
        for r in conflicts:
            print(
                f"  {r['name']} ({r['object_type']}): "
                f"confidence ranges {r['min_confidence']:.2f}-{r['max_confidence']:.2f}"
            )

        print("\nDuplicate candidates (review suggested):")
        dups = know_analytics.duplicate_candidates(conn, limit=10)
        if not dups:
            print("  (none)")
        for d in dups:
            print(
                f"  [{d['similarity']:.2f}] {d['left_name']} <-> {d['right_name']} "
                f"({d['object_type']})"
            )


def _cmd_knowledge_growth(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        trend = know_analytics.growth_trend(
            conn, interval=args.interval, limit=args.limit
        )
    print(f"Knowledge growth (by {trend['interval']}):")
    if not trend["points"]:
        print("  (no dated knowledge yet - run: catalog consolidate)")
        return
    for p in trend["points"]:
        print(
            f"  {p['period']}  "
            f"objects +{_fmt(p['objects_added'])} (total {_fmt(p['objects_total'])})  "
            f"relationships +{_fmt(p['relationships_added'])} "
            f"(total {_fmt(p['relationships_total'])})  "
            f"artifacts +{_fmt(p['artifacts_added'])} (total {_fmt(p['artifacts_total'])})"
        )


def _cmd_show_object(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        obj = know_repo.get_object(conn, args.object_id)
        if obj is None:
            print(f"No knowledge object with id {args.object_id!r}.")
            return
        mentions = know_repo.mentions_for_object(conn, args.object_id)
        evidence = know_repo.evidence_for_object(conn, args.object_id)
        rels = know_repo.relationships_for_object(conn, args.object_id)

    documents = len({m["artifact_id"] for m in mentions})
    print(f"{obj['canonical_name']}")
    print(f"\nId: {obj['id']}")
    print(f"Type: {obj['object_type']}")
    print(f"Confidence: {obj['confidence']:.2f}")
    print(f"Status: {obj['status']}    Merge confidence: {obj['merge_confidence']:.2f}")
    print(f"Mentions: {_fmt(len(mentions))}")
    print(f"Documents: {_fmt(documents)}")
    if obj["description"]:
        print(f"\nDescription:\n{obj['description']}")

    print("\nRelated objects:")
    if not rels:
        print("  (none)")
    for r in rels:
        if r["source_object"] == args.object_id:
            print(f"  {r['predicate']} -> {r['target_object']} "
                  f"[{r['confidence']:.2f}] ({r['review_status']}) id={r['id']}")
        else:
            print(f"  {r['source_object']} -> {r['predicate']} (this) "
                  f"[{r['confidence']:.2f}] ({r['review_status']}) id={r['id']}")

    print("\nEvidence:")
    if not evidence:
        print("  (none)")
    for e in evidence[:15]:
        locator = ""
        if e["slide_number"] is not None:
            locator = f" slide {e['slide_number']}"
        elif e["page_number"] is not None:
            locator = f" page {e['page_number']}"
        quote = e["quote"] or ""
        print(f"  {e['artifact_id']}{locator}: \"{quote}\"")


def _cmd_search_knowledge(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = know_repo.search_objects(conn, args.query)
    if not rows:
        print(f"No knowledge objects match {args.query!r}.")
        return
    for r in rows:
        print(
            f"[{r['confidence']:.2f}] {r['canonical_name']} "
            f"({r['object_type']}, {r['status']})  id={r['id']}"
        )


def _cmd_review_candidates(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        objects = know_repo.objects_by_status(conn, ReviewState.PROPOSED.value)
        rels = know_repo.relationships_by_status(conn, ReviewState.PROPOSED.value)
        dups = know_analytics.duplicate_candidates(conn, limit=20)

    print(f"Proposed objects awaiting review: {_fmt(len(objects))}")
    for r in objects[:30]:
        print(f"  [{r['confidence']:.2f}] {r['canonical_name']} ({r['object_type']})  id={r['id']}")

    print(f"\nProposed relationships awaiting review: {_fmt(len(rels))}")
    for r in rels[:30]:
        print(f"  [{r['confidence']:.2f}] {r['source_object']} {r['predicate']} {r['target_object']}  id={r['id']}")

    print(f"\nDuplicate candidates (possible merges): {_fmt(len(dups))}")
    for d in dups:
        print(
            f"  [{d['similarity']:.2f}] {d['left_name']} <-> {d['right_name']} "
            f"({d['object_type']})"
        )


def _cmd_review_object(args, status: str) -> None:
    init_db(args.db)
    changed = review_object(args.db, args.object_id, status)
    if changed:
        print(f"{args.object_id} -> {status}")
    else:
        print(f"No knowledge object with id {args.object_id!r}.")


def _cmd_review_relationship(args, status: str) -> None:
    init_db(args.db)
    changed = review_relationship(args.db, args.relationship_id, status)
    if changed:
        print(f"relationship {args.relationship_id} -> {status}")
    else:
        print(f"No knowledge relationship with id {args.relationship_id!r}.")


def _cmd_approve_confidence_interval(args) -> None:
    init_db(args.db)
    object_statuses = [ReviewState.PROPOSED.value]
    relationship_statuses = [ReviewState.PROPOSED.value]
    if args.include_reviewed:
        object_statuses.append(ReviewState.REVIEWED.value)
        relationship_statuses.append(ReviewState.REVIEWED.value)

    objects_approved = 0
    relationships_approved = 0
    try:
        if args.target in {"objects", "all"}:
            for status in object_statuses:
                stats = gov_service.approve_objects_by_confidence(
                    args.db,
                    args.min_confidence,
                    args.max_confidence,
                    reviewer="cli",
                    note=args.note,
                    current_status=status,
                )
                objects_approved += stats.objects_approved
        if args.target in {"relationships", "all"}:
            for status in relationship_statuses:
                stats = approve_relationships_by_confidence(
                    args.db,
                    args.min_confidence,
                    args.max_confidence,
                    reviewer="cli",
                    note=args.note,
                    current_status=status,
                )
                relationships_approved += stats.relationships_approved
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    print(
        "Approved by confidence interval "
        f"[{args.min_confidence:.2f}, {args.max_confidence:.2f}]:"
    )
    print(f"  Objects approved: {_fmt(objects_approved)}")
    print(f"  Relationships approved: {_fmt(relationships_approved)}")


def _cmd_export_graph_json(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        paths = export_graph_json(conn)
    with connect(args.db) as conn:
        nodes = know_repo.count_objects(conn)
        edges = know_repo.count_table(conn, "knowledge_relationships")
    print("Graph exported:")
    print(f"  {paths['nodes']} ({_fmt(nodes)} nodes)")
    print(f"  {paths['edges']} ({_fmt(edges)} edges)")


def _cmd_rdf_export(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        paths = export_rdf(conn, args.out, fmt=args.format)
        stats = rdf_stats(conn)
    print("RDF export complete:")
    for name in ("ontology", "knowledge", "relationships", "provenance"):
        print(f"  {paths[name]}")
    print(f"\nObjects exported: {_fmt(stats['objects'])}")
    print(f"Relationships exported: {_fmt(stats['relationships'])}")
    print(f"Evidence exported: {_fmt(stats['evidence'])}")


def _cmd_rdf_validate(args) -> None:
    results = validate_rdf(args.out)
    if not results:
        print(f"No RDF files found in {args.out}. Run: catalog rdf-export")
        return
    all_ok = True
    for name, result in results.items():
        if result["ok"]:
            print(f"  OK    {name} ({_fmt(result['triples'])} triples)")
        else:
            all_ok = False
            print(f"  FAIL  {name}: {result['error']}")
    print("\nAll files valid." if all_ok else "\nValidation failed.")


def _cmd_rdf_stats(args) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        stats = rdf_stats(conn)
    print("RDF projection (APPROVED knowledge only):")
    print(f"Objects exported: {_fmt(stats['objects'])}")
    print(f"Relationships exported: {_fmt(stats['relationships'])}")
    print(f"Evidence exported: {_fmt(stats['evidence'])}")
    print(
        f"\nTriples - knowledge: {_fmt(stats['knowledge_triples'])}  "
        f"relationships: {_fmt(stats['relationship_triples'])}  "
        f"provenance: {_fmt(stats['provenance_triples'])}"
    )
    if stats["objects"] == 0:
        print("\nNo approved objects yet. Approve some: catalog approve-object <id>")


def _cmd_fuseki_load(args) -> None:
    config = load_jena_config(args.jena_config)
    print(f"Loading RDF into Fuseki at {config.endpoint} ...")
    try:
        uploaded = fuseki_load(config, args.out)
    except FusekiError as exc:
        print(f"Error: {exc}")
        return
    print("Upload complete:")
    for name in ("ontology", "knowledge", "relationships", "provenance"):
        if name in uploaded:
            print(f"  {name}: {_fmt(uploaded[name])} triples")


def _cmd_fuseki_clear(args) -> None:
    config = load_jena_config(args.jena_config)
    try:
        clear_dataset(config)
    except FusekiError as exc:
        print(f"Error: {exc}")
        return
    print(f"Cleared all triples from {config.endpoint}")


def _cmd_benchmark(args) -> int:
    """Dispatch to the benchmark harness (kept in the top-level ``benchmarks`` package)."""

    try:
        from benchmarks.runner import main as run_benchmarks
    except ImportError as exc:
        print(
            "Error: the benchmark suite is unavailable "
            f"(could not import 'benchmarks': {exc}).\n"
            "Run from the repository root or reinstall with 'pip install -e .'."
        )
        return 1

    argv = ["--stages", args.stages, "--provider", args.provider, "--format", args.format]
    argv += ["--llm-config", args.llm_config]
    if args.out:
        argv += ["--out", args.out]
    if args.thresholds:
        argv += ["--thresholds", args.thresholds]
    if args.check:
        argv.append("--check")
    return run_benchmarks(argv)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.command == "init-db":
        init_db(args.db)
        print(f"Initialized {args.db}")
    elif args.command == "api":
        from .api.server import run as run_api

        run_api(
            host=args.host,
            port=args.port,
            reload=args.reload,
            config_path=args.api_config,
            db_path=args.db,
            cache_dir=args.cache,
        )
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
    elif args.command == "consolidate":
        _cmd_consolidate(args)
    elif args.command == "knowledge-stats":
        _cmd_knowledge_stats(args)
    elif args.command == "knowledge-growth":
        _cmd_knowledge_growth(args)
    elif args.command == "show-object":
        _cmd_show_object(args)
    elif args.command == "search-knowledge":
        _cmd_search_knowledge(args)
    elif args.command == "review-candidates":
        _cmd_review_candidates(args)
    elif args.command == "approve-object":
        _cmd_review_object(args, ReviewState.APPROVED.value)
    elif args.command == "reject-object":
        _cmd_review_object(args, ReviewState.REJECTED.value)
    elif args.command == "approve-relationship":
        _cmd_review_relationship(args, ReviewState.APPROVED.value)
    elif args.command == "approve-confidence-interval":
        _cmd_approve_confidence_interval(args)
    elif args.command == "reject-relationship":
        _cmd_review_relationship(args, ReviewState.REJECTED.value)
    elif args.command == "export-graph-json":
        _cmd_export_graph_json(args)
    elif args.command == "rdf-export":
        _cmd_rdf_export(args)
    elif args.command == "rdf-validate":
        _cmd_rdf_validate(args)
    elif args.command == "rdf-stats":
        _cmd_rdf_stats(args)
    elif args.command == "fuseki-load":
        _cmd_fuseki_load(args)
    elif args.command == "fuseki-clear":
        _cmd_fuseki_clear(args)
    elif args.command == "graph":
        run_graph(args)
    elif args.command == "governance":
        run_governance(args)
    elif args.command in {"ask", "explain", "impact", "compare", "path-reason"}:
        run_graphrag(args)
    elif args.command == "benchmark":
        return _cmd_benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
