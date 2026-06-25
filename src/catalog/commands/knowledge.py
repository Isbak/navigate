"""Knowledge-graph commands: consolidation, lifecycle, stats and read-only
displays of classified candidates and consolidated objects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import load_config
from ..cost import record_calls
from ..db import connect, init_db
from ..knowledge import analytics as know_analytics
from ..knowledge import repository as know_repo
from ..knowledge.export import export_graph_json
from ..knowledge.models import ReviewState
from ..knowledge.prompts import make_merge_judge
from ..knowledge.resolution import ResolutionConfig
from ..knowledge.service import consolidate
from ..maintenance import purge_path
from ..semantic import repository as sem_repo
from ..semantic.config import load_llm_config
from ..semantic.providers import LLMError, build_provider
from ._common import fmt


def _cmd_show_summary(args: argparse.Namespace) -> None:
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


def _cmd_show_decisions(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = sem_repo.decisions(conn, args.min_confidence)
    for row in rows:
        quote = f'  — "{row["supporting_text"]}"' if row["supporting_text"] else ""
        print(f"[{row['confidence']:.2f}] {row['decision_text']} ({row['artifact_id']}){quote}")
    if not rows:
        print("No candidate decisions.")


def _cmd_show_risks(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = sem_repo.risks(conn, args.min_confidence)
    for row in rows:
        quote = f'  — "{row["supporting_text"]}"' if row["supporting_text"] else ""
        print(f"[{row['confidence']:.2f}] {row['risk_description']} ({row['artifact_id']}){quote}")
    if not rows:
        print("No candidate risks.")


def _cmd_show_capabilities(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = sem_repo.capabilities(conn, args.min_confidence)
    for row in rows:
        print(f"[{row['confidence']:.2f}] {row['name']} ({row['artifact_id']})")
    if not rows:
        print("No candidate capabilities.")


def _cmd_show_relationships(args: argparse.Namespace) -> None:
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


def _resolve_source_scope(config_path: str, all_sources: bool) -> list[str | Path] | None:
    """Source-folder paths that scope consolidation, or None for no scoping.

    ``--all-sources`` opts out (legacy unscoped behavior). A missing config falls
    back to an empty scope: only curated imports (which have no file path) are
    consolidated, and a warning is printed.
    """

    if all_sources:
        return None
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        print(
            f"Warning: config {config_path} not found; consolidating curated "
            "standards only. Use --all-sources for the legacy behavior."
        )
        return []
    paths: list[str | Path] = [source.path for source in cfg.sources]
    return paths


def _cmd_consolidate(args: argparse.Namespace) -> None:
    merge_judge = None
    merge_usage: list = []
    merge_provider_name = None
    if args.use_llm:
        config = load_llm_config(args.llm_config)
        try:
            provider = build_provider(config)
        except LLMError as exc:
            print(f"Error: {exc}")
            return
        print(f"Using {config.provider} model {provider.model} for merge suggestions ...")
        merge_provider_name = config.provider
        merge_judge = make_merge_judge(provider, usage_sink=merge_usage)

    resolution_config = None
    if args.min_confidence is not None:
        resolution_config = ResolutionConfig(min_mention_confidence=args.min_confidence)

    source_paths = _resolve_source_scope(args.config, args.all_sources)
    if source_paths is None:
        print("Consolidating knowledge objects (all sources) ...")
    else:
        print(
            f"Consolidating knowledge objects from {len(source_paths)} "
            "configured source folder(s) ..."
        )
    stats = consolidate(
        args.db,
        cache_dir=args.cache,
        force=args.force,
        config=resolution_config,
        merge_judge=merge_judge,
        source_paths=source_paths,
    )
    record_calls(args.db, merge_usage, operation="merge", provider_name=merge_provider_name)
    print("Consolidation complete:")
    print(f"Mentions gathered: {fmt(stats.mentions_gathered)}")
    print(f"Knowledge objects: {fmt(stats.objects_created)}")
    print(f"Mentions linked: {fmt(stats.mentions_linked)}")
    print(f"Evidence rows: {fmt(stats.evidence_created)}")
    print(f"Relationships: {fmt(stats.relationships_created)}")
    print(f"  structural (appears_in): {fmt(stats.relationships_structural)}")
    print(f"  cross-references: {fmt(stats.relationships_crossref)}")
    print(f"Relationships unresolved: {fmt(stats.relationships_unresolved)}")
    print(f"Floating objects (no edge): {fmt(stats.objects_floating)}")
    if not args.force:
        print(f"Review statuses preserved: {fmt(stats.statuses_preserved)}")
    if stats.by_object_type:
        print("\nObjects by type:")
        for otype, count in sorted(stats.by_object_type.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {otype}: {fmt(count)}")


def _cmd_clean_source(args: argparse.Namespace) -> None:
    print(f"Purging all material under {args.path} ...")
    stats = purge_path(
        args.path,
        db_path=args.db,
        cache_dir=args.cache,
        config_path=args.config,
        reconsolidate=args.reconsolidate,
        all_sources=args.all_sources,
    )
    if stats.artifact_rows_deleted == 0:
        print("No indexed artifacts under that path. Nothing to purge.")
        return
    print("Purge complete:")
    print(f"Artifact rows deleted: {fmt(stats.artifact_rows_deleted)}")
    print(f"Artifacts purged: {fmt(stats.artifacts_purged)}")
    print(f"Links deleted: {fmt(stats.links_deleted)}")
    print(f"Cache dirs removed: {fmt(stats.cache_dirs_removed)}")
    if stats.artifacts_shared:
        print(f"Artifacts kept (duplicate copy survives elsewhere): {fmt(stats.artifacts_shared)}")
        for artifact_id in stats.shared_ids:
            print(f"  kept: {artifact_id}")
    if stats.reconsolidated:
        print("Re-consolidated the knowledge graph.")
    else:
        print("Skipped re-consolidation. Run: catalog consolidate")


def _cmd_knowledge_stats(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        total = know_repo.count_objects(conn)
        print(f"Knowledge objects: {fmt(total)}")
        if total == 0:
            print("No knowledge objects yet. Run: catalog consolidate")
            return

        print(f"Mentions: {fmt(know_repo.count_table(conn, 'knowledge_mentions'))}")
        print(f"Evidence: {fmt(know_repo.count_table(conn, 'knowledge_evidence'))}")
        print(f"Relationships: {fmt(know_repo.count_table(conn, 'knowledge_relationships'))}")

        print("\nObjects by type:")
        for row in know_repo.object_type_counts(conn):
            print(f"  {row['key']}: {fmt(row['count'])}")

        print("\nObjects by review status:")
        for status in (s.value for s in ReviewState):
            count = len(know_repo.objects_by_status(conn, status))
            print(f"  {status}: {fmt(count)}")

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
                print(f"  {fmt(r['documents'])} docs  [{r['confidence']:.2f}]  {r['name']}")

        print("\nMost connected objects:")
        connected = know_analytics.most_connected(conn, 10)
        if not connected:
            print("  (none)")
        for r in connected:
            print(f"  {fmt(r['degree'])} links  {r['name']} ({r['object_type']})")

        print("\nMost mentioned objects:")
        for r in know_analytics.most_mentioned(conn, 10):
            print(f"  {fmt(r['documents'])} docs  {r['name']} ({r['object_type']})")

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


def _cmd_knowledge_growth(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        trend = know_analytics.growth_trend(conn, interval=args.interval, limit=args.limit)
    print(f"Knowledge growth (by {trend['interval']}):")
    if not trend["points"]:
        print("  (no dated knowledge yet - run: catalog consolidate)")
        return
    for p in trend["points"]:
        print(
            f"  {p['period']}  "
            f"objects +{fmt(p['objects_added'])} (total {fmt(p['objects_total'])})  "
            f"relationships +{fmt(p['relationships_added'])} "
            f"(total {fmt(p['relationships_total'])})  "
            f"artifacts +{fmt(p['artifacts_added'])} (total {fmt(p['artifacts_total'])})"
        )


def _cmd_export_graph_json(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        paths = export_graph_json(conn)
    with connect(args.db) as conn:
        nodes = know_repo.count_objects(conn)
        edges = know_repo.count_table(conn, "knowledge_relationships")
    print("Graph exported:")
    print(f"  {paths['nodes']} ({fmt(nodes)} nodes)")
    print(f"  {paths['edges']} ({fmt(edges)} edges)")


def register(sub: argparse._SubParsersAction) -> None:
    summary = sub.add_parser("show-summary")
    summary.add_argument("--artifact-id", required=True)
    summary.set_defaults(func=_cmd_show_summary)

    for name, handler in (
        ("show-decisions", _cmd_show_decisions),
        ("show-risks", _cmd_show_risks),
        ("show-capabilities", _cmd_show_capabilities),
        ("show-relationships", _cmd_show_relationships),
    ):
        p = sub.add_parser(name)
        p.add_argument("--min-confidence", type=float, default=0.0)
        p.set_defaults(func=handler)

    # -- knowledge consolidation layer --
    cons = sub.add_parser("consolidate")
    cons.add_argument("--force", action="store_true")
    cons.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="ignore candidate mentions weaker than this (0.0-1.0); defaults to "
        "the built-in noise floor",
    )
    cons.add_argument(
        "--use-llm",
        action="store_true",
        help="consult the configured LLM for borderline merge suggestions",
    )
    cons.add_argument(
        "--all-sources",
        action="store_true",
        help="consolidate every classified document, ignoring the source-folder "
        "scope in the config (legacy behavior)",
    )
    cons.set_defaults(func=_cmd_consolidate)

    clean = sub.add_parser(
        "clean-source",
        help="permanently remove all material tied to a file or folder",
    )
    clean.add_argument("--path", required=True, help="file or folder whose material to purge")
    clean.add_argument(
        "--no-reconsolidate",
        dest="reconsolidate",
        action="store_false",
        help="skip the automatic re-consolidation after purging",
    )
    clean.add_argument(
        "--all-sources",
        action="store_true",
        help="re-consolidate unscoped (legacy) instead of by configured sources",
    )
    clean.set_defaults(reconsolidate=True, func=_cmd_clean_source)

    sub.add_parser("knowledge-stats").set_defaults(func=_cmd_knowledge_stats)

    growth = sub.add_parser("knowledge-growth", help="knowledge-growth trend over time")
    growth.add_argument("--interval", choices=("day", "week", "month"), default="month")
    growth.add_argument("--limit", type=int, default=12)
    growth.set_defaults(func=_cmd_knowledge_growth)

    sub.add_parser("export-graph-json").set_defaults(func=_cmd_export_graph_json)


__all__ = ["register"]
