"""Semantic-classification commands: ``classify``, ``classification-stats``,
``cost-report``."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import load_performance_config, resolve_workers
from ..cost import repository as cost_repo
from ..db import connect, init_db
from ..semantic import analytics as sem_analytics
from ..semantic import repository as sem_repo
from ..semantic.config import load_llm_config
from ..semantic.providers import LLMError, build_provider
from ..semantic.routing import build_router
from ..semantic.service import classify_documents
from ._common import fmt


def _cmd_classify(args: argparse.Namespace) -> None:
    config = load_llm_config(args.llm_config)
    try:
        provider = build_provider(config)
        router = build_router(config, factory=build_provider)
    except LLMError as exc:
        print(f"Error: {exc}")
        return
    if config.routing.enabled:
        print(
            f"Classifying with {config.provider}: adaptive routing "
            f"({config.routing.fast_model} -> {config.routing.deep_model}) ..."
        )
    else:
        print(f"Classifying with {config.provider} model {provider.model} ...")

    def _show_progress(completed: int, total: int, artifact_id: str) -> None:
        percent = round((completed / total) * 100) if total else 100
        print(f"Classification progress: {percent}% complete ({completed}/{total}) {artifact_id}")

    perf = load_performance_config(args.performance_config)
    workers = resolve_workers(args.workers, perf.classify_workers)

    # A fresh router per worker thread keeps each provider's (mutable) usage
    # state thread-local so concurrent token accounting stays correct.
    def _router_factory() -> Any:
        return build_router(config, factory=build_provider)

    stats = classify_documents(
        db_path=args.db,
        cache_dir=args.cache,
        provider=provider,
        artifact_id=args.artifact_id,
        force=args.force,
        max_input_chars=config.max_input_chars,
        chunk_overlap=config.chunk_overlap,
        max_chunks=config.max_chunks,
        progress_callback=_show_progress,
        provider_name=config.provider,
        router=router if workers <= 1 else None,
        router_factory=_router_factory if workers > 1 else None,
        workers=workers,
    )
    print("Classification complete:")
    print(f"Documents processed: {fmt(stats.documents_processed)}")
    print(f"Documents skipped (unchanged): {fmt(stats.documents_skipped)}")
    print(f"Errors: {fmt(stats.errors)}")
    print(f"Candidate entities: {fmt(stats.entities)}")
    print(f"Candidate capabilities: {fmt(stats.capabilities)}")
    print(f"Candidate decisions: {fmt(stats.decisions)}")
    print(f"Candidate risks: {fmt(stats.risks)}")
    print(f"Candidate relationships: {fmt(stats.relationships)}")
    if stats.by_document_type:
        print("\nDocument types (this run):")
        for dtype, count in sorted(stats.by_document_type.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {dtype}: {fmt(count)}")


def _print_ranked(title: str, rows: list[dict], value_key: str, name_key: str = "name") -> None:
    print(f"\n{title}:")
    if not rows:
        print("  (none)")
        return
    for row in rows:
        print(f"  {fmt(row[value_key])}  {row[name_key]}")


def _cmd_classification_stats(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        total = sem_repo.count_classifications(conn)
        print(f"Classified documents: {fmt(total)}")
        if total == 0:
            print("No classifications yet. Run: catalog classify")
            return

        print("\nDocument types:")
        for row in sem_repo.document_type_counts(conn):
            print(f"  {row['key']}: {fmt(row['count'])}")

        print("\nCandidate objects (knowledge proposed, not facts):")
        for table, label in (
            ("candidate_entities", "entities"),
            ("candidate_capabilities", "capabilities"),
            ("candidate_decisions", "decisions"),
            ("candidate_risks", "risks"),
            ("candidate_relationships", "relationships"),
        ):
            print(f"  {label}: {fmt(sem_repo.count_rows(conn, table))}")

        _print_ranked(
            "Top domains (by documents)", sem_analytics.top_domains(conn, 10), "documents"
        )
        _print_ranked(
            "Top capabilities (by documents)", sem_analytics.top_capabilities(conn, 10), "documents"
        )
        _print_ranked(
            "Most common technologies", sem_analytics.top_technologies(conn, 10), "documents"
        )
        _print_ranked("Most referenced concepts", sem_analytics.top_concepts(conn, 10), "documents")

        print("\nMost common decision themes:")
        themes = sem_analytics.decision_themes(conn, 10)
        if not themes:
            print("  (none)")
        for t in themes:
            print(f"  {fmt(t['documents'])} docs  {t['text']}")

        print("\nRisks across multiple documents:")
        risks = [r for r in sem_analytics.risk_themes(conn, 20) if r["documents"] > 1]
        if not risks:
            print("  (none)")
        for r in risks[:10]:
            print(f"  {fmt(r['documents'])} docs  {r['text']}")

        print("\nConcepts connecting multiple domains:")
        concepts = sem_analytics.concepts_connecting_domains(conn, min_domains=2, limit=10)
        if not concepts:
            print("  (none)")
        for c in concepts:
            print(f"  {c['name']} -> {', '.join(c['domains'])}")


def _usd(value: float | None) -> str:
    return f"${value:,.4f}" if value is not None else "-"


def _row_to_dict(row: sqlite3.Row) -> dict:
    # sqlite3.Row iterates values, not keys, so .keys() is required here (a Row
    # is not a dict; SIM118 does not apply).
    return {key: row[key] for key in row.keys()}  # noqa: SIM118


def _cost_report_data(conn: sqlite3.Connection, top: int) -> dict:
    t = cost_repo.totals(conn)
    return {
        "totals": _row_to_dict(t),
        "by_operation": [_row_to_dict(r) for r in cost_repo.by_operation(conn)],
        "by_model": [_row_to_dict(r) for r in cost_repo.by_model(conn)],
        "cost_per_document": [_row_to_dict(r) for r in cost_repo.cost_per_document(conn, top)],
        "cost_vs_quality": [_row_to_dict(r) for r in cost_repo.cost_vs_quality(conn, top)],
    }


def _render_cost_table(data: dict) -> None:
    t = data["totals"]
    if not t["calls"]:
        print(
            "No LLM usage recorded yet. Run: catalog classify "
            "(or catalog extract --mode high-quality)"
        )
        return

    print("LLM cost report (cost of extraction)")
    print(f"Total calls: {fmt(t['calls'])}")
    print(f"Input tokens: {fmt(t['input_tokens'])}")
    print(f"Output tokens: {fmt(t['output_tokens'])}")
    print(f"Total tokens: {fmt(t['total_tokens'])}")
    if t["cache_read_tokens"] or t["cache_write_tokens"]:
        print(
            f"Cache tokens (read/write): {fmt(t['cache_read_tokens'])}"
            f" / {fmt(t['cache_write_tokens'])}"
        )
    print(f"Total cost: {_usd(t['cost_usd'])}")
    if t["unpriced_calls"]:
        print(f"Unpriced calls (no rate in pricing.yml): {fmt(t['unpriced_calls'])}")

    print("\nBy operation:")
    for r in data["by_operation"]:
        print(
            f"  {r['key']}: {fmt(r['calls'])} calls, "
            f"{fmt(r['total_tokens'])} tokens, {_usd(r['cost_usd'])}"
        )

    print("\nBy model:")
    for r in data["by_model"]:
        marker = " (unpriced)" if r["cost_usd"] is None else ""
        print(
            f"  {r['key']}{marker}: {fmt(r['calls'])} calls, "
            f"{fmt(r['total_tokens'])} tokens, {_usd(r['cost_usd'])}"
        )

    print(f"\nCost per document (top {len(data['cost_per_document'])}):")
    if not data["cost_per_document"]:
        print("  (none)")
    for r in data["cost_per_document"]:
        print(f"  {_usd(r['cost_usd'])}  {fmt(r['total_tokens'])} tokens  {r['key']}")

    print("\nCost vs. quality (spend beside the model's classification confidence):")
    rows = data["cost_vs_quality"]
    if not rows:
        print("  (none)")
    for r in rows:
        conf = r["type_confidence"]
        conf_s = f"{conf:.2f}" if conf is not None else "n/a"
        dtype = r["document_type"] or "unclassified"
        print(f"  {_usd(r['cost_usd'])}  conf {conf_s}  [{dtype}]  {r['key']}")


def _cmd_cost_report(args: argparse.Namespace) -> None:
    # The report reads the cost_usd persisted when each call was recorded (priced
    # from config/pricing.yml at that time), so editing rates affects only future
    # calls - past spend stays as it was actually billed.
    init_db(args.db)
    with connect(args.db) as conn:
        data = _cost_report_data(conn, args.top)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"Wrote cost report to {out_path}")

    if args.format == "json":
        if not args.out:
            print(json.dumps(data, indent=2))
        return

    _render_cost_table(data)


def register(sub: argparse._SubParsersAction) -> None:
    classify = sub.add_parser("classify")
    classify.add_argument(
        "--artifact-id",
        action="append",
        default=None,
        help="classify only this artifact id (repeatable)",
    )
    classify.add_argument("--force", action="store_true")
    classify.add_argument(
        "--workers",
        type=int,
        default=None,
        help="parallel classification workers for concurrent LLM calls "
        "(default from config/performance.yml; 0 = one per CPU)",
    )
    classify.set_defaults(func=_cmd_classify)

    sub.add_parser("classification-stats").set_defaults(func=_cmd_classification_stats)

    cost = sub.add_parser(
        "cost-report", help="report LLM token usage and cost (cost of extraction)"
    )
    cost.add_argument("--format", choices=["table", "json"], default="table")
    cost.add_argument("--out", default=None, help="write the JSON report to this path")
    cost.add_argument(
        "--top", type=int, default=20, help="rows in per-document sections (default 20)"
    )
    cost.set_defaults(func=_cmd_cost_report)


__all__ = ["register"]
