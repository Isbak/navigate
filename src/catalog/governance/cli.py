"""CLI surface for the governance layer (``catalog governance ...``).

Wires the governance engine into the command line, rendering with Rich to match
the ``catalog graph`` command group. Everything runs against the local SQLite
system of record; run ``catalog governance scan`` first to make the lifecycle,
quality, and alert numbers current.
"""

from __future__ import annotations

import argparse
import dataclasses

from ..db import connect, init_db
from . import dashboard as dashboard_mod
from . import domains as domain_analysis
from . import export as export_mod
from . import orphans as orphan_mod
from . import repository as repo
from .agent_review import agent_approve, revert_agent_actions, revert_review
from .config import load_governance_config
from .models import OPEN_REVIEW_STATES, FreshnessState
from .ownership import assign_owner
from .service import (
    approve_object,
    archive_object,
    flag_object,
    reject_object,
    run_scan,
)


def _console():
    from rich.console import Console

    return Console(width=120, highlight=False)


def _config(args):
    return load_governance_config(getattr(args, "governance_config", "config/governance.yml"))


# -- command handlers ---------------------------------------------------------

def _cmd_scan(args) -> None:
    console = _console()
    stats = run_scan(args.db, _config(args))
    console.print("[bold]Governance scan complete[/bold]")
    d = stats.as_dict()
    console.print(
        f"Objects seen: {d['objects_seen']}    "
        f"added: {d['objects_added']}    removed: {d['objects_removed']}"
    )
    console.print(
        f"Relationships added: {d['relationships_added']}    "
        f"removed: {d['relationships_removed']}"
    )
    console.print(
        f"Confidence changes: {d['confidence_changes']}    "
        f"freshness transitions: {d['freshness_transitions']}"
    )
    console.print(
        f"Drift findings: {d['drift_findings']}    "
        f"quality degradations: {d['quality_degradations']}"
    )
    console.print(f"Alerts generated: {d['alerts_generated']}")


def _cmd_dashboard(args) -> None:
    console = _console()
    config = _config(args)
    init_db(args.db)
    with connect(args.db) as conn:
        data = dashboard_mod.build_dashboard(conn, config)

    console.print("[bold]Knowledge Health Dashboard[/bold]")
    console.print(f"Knowledge objects: {data['knowledge_objects']}")
    console.print(f"Approved objects:  {data['approved_objects']}")
    console.print(f"Pending reviews:   {data['pending_reviews']}")
    console.print(f"Stale objects:     {data['stale_objects']}")
    console.print(f"Fresh objects:     {data['fresh_objects']}")
    console.print(f"Average quality:   {data['average_quality']}")
    console.print(f"Open alerts:       {data['open_alerts']}")

    console.print("\n[bold]Top domains[/bold]:")
    if not data["top_domains"]:
        console.print("  (none)")
    for d in data["top_domains"]:
        console.print(
            f"  {d['domain']}: {d['object_count']} objects, "
            f"quality {d['avg_quality']}"
        )

    console.print("\n[bold]Recent changes[/bold]:")
    if not data["recent_changes"]:
        console.print("  (none)")
    for c in data["recent_changes"]:
        console.print(f"  {c['change_type']}  {c['object_id'] or ''}  {c['detail']}")


def _cmd_review_queue(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.lifecycle_by_review(conn, OPEN_REVIEW_STATES)
        quality = repo.quality_map(conn)
    console.print(f"[bold]Review queue[/bold] ({len(rows)} object(s)):")
    if not rows:
        console.print("  (nothing waiting)")
    for r in rows:
        q = quality.get(r["object_id"])
        score = f"q={q['quality_score']}" if q else "q=?"
        console.print(
            f"  [{r['review_state']}] {r['name'] or r['object_id']} "
            f"({r['object_type']})  {score}  id={r['object_id']}"
        )


def _cmd_stale(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.lifecycle_by_freshness(
            conn, (FreshnessState.STALE.value, FreshnessState.ARCHIVED.value)
        )
        aging = repo.lifecycle_by_freshness(conn, (FreshnessState.AGING.value,))
    console.print(f"[bold]Stale knowledge[/bold] ({len(rows)} object(s)):")
    if not rows:
        console.print("  (none)")
    for r in rows:
        console.print(
            f"  [{r['freshness_state']}] {r['name'] or r['object_id']} "
            f"(freshness {r['freshness_score']:.2f})  id={r['object_id']}"
        )
    console.print(f"\n[bold]Aging[/bold] ({len(aging)} object(s)):")
    if not aging:
        console.print("  (none)")
    for r in aging:
        console.print(
            f"  {r['name'] or r['object_id']} (freshness {r['freshness_score']:.2f})"
        )


def _cmd_quality(args) -> None:
    console = _console()
    config = _config(args)
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.quality_ranked(conn, ascending=args.ascending)
        avg = repo.average_quality(conn)
    console.print(f"[bold]Quality scores[/bold] (average {avg}):")
    if not rows:
        console.print("  (none - run: catalog governance scan)")
    threshold = config.quality.low_quality_threshold
    for r in rows[: args.limit]:
        flag = "  [low]" if (r["quality_score"] or 0) < threshold else ""
        console.print(
            f"  {r['quality_score']:>5}  {r['canonical_name']} "
            f"({r['object_type']}){flag}  id={r['object_id']}"
        )


def _cmd_orphaned(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        report = orphan_mod.all_orphans(conn)

    def _section(title: str, items: list, render) -> None:
        console.print(f"\n[bold]{title}[/bold] ({len(items)}):")
        if not items:
            console.print("  (none)")
        for item in items[:20]:
            console.print(f"  {render(item)}")

    _section("Objects without evidence", report["objects_without_evidence"],
             lambda o: f"{o['name']} ({o['type']})")
    _section("Objects without relationships", report["objects_without_relationships"],
             lambda o: f"{o['name']} ({o['type']})")
    _section("Objects without owner", report["objects_without_owner"],
             lambda o: f"{o['name']} ({o['type']})")
    _section("Relationships without evidence", report["relationships_without_evidence"],
             lambda r: f"{r['source']} {r['predicate']} {r['target']}")
    _section("Evidence without object", report["evidence_without_object"],
             lambda e: f"evidence {e['id']} -> missing {e['object_id']}")


def _cmd_alerts(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.open_alerts(conn, args.type)
        counts = repo.count_open_alerts_by_type(conn)
    console.print(f"[bold]Open alerts[/bold] ({len(rows)}):")
    if not rows:
        console.print("  (none)")
    for r in rows[: args.limit]:
        console.print(
            f"  [{r['severity']}] {r['alert_type']}: {r['message']}"
        )
    if counts and not args.type:
        console.print("\n[bold]By type[/bold]:")
        for c in counts:
            console.print(f"  {c['key']}: {c['count']}")


def _cmd_history(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        from ..knowledge import repository as know_repo

        obj = know_repo.get_object(conn, args.object)
        changes = repo.changes_for_object(conn, args.object)
        reviews = conn.execute(
            "SELECT * FROM knowledge_reviews WHERE target_id = ? AND target_kind = 'object' "
            "ORDER BY id",
            (args.object,),
        ).fetchall()
        life = repo.get_lifecycle(conn, args.object)
        owner = repo.get_owner(conn, args.object)

    name = obj["canonical_name"] if obj else args.object
    console.print(f"[bold]History: {name}[/bold]  (id={args.object})")
    if life:
        console.print(
            f"Review state: {life['review_state']}    "
            f"Freshness: {life['freshness_state']} ({life['freshness_score']:.2f})"
        )
        console.print(
            f"Created: {life['created_at']}    Last seen: {life['last_seen_at']}    "
            f"Last reviewed: {life['last_reviewed_at'] or '(never)'}"
        )
    if owner:
        console.print(f"Owner: {owner['owner_type']} / {owner['owner_id']}")

    console.print("\n[bold]Change log[/bold]:")
    if not changes:
        console.print("  (none)")
    for c in changes:
        change = f"{c['old_value']} -> {c['new_value']}" if c["new_value"] or c["old_value"] else ""
        console.print(
            f"  {c['detected_at']}  {c['change_type']}  {change}  {c['detail']}".rstrip()
        )

    console.print("\n[bold]Review actions[/bold]:")
    if not reviews:
        console.print("  (none)")
    for r in reviews:
        note = f": {r['note']}" if r["note"] else ""
        console.print(f"  {r['created_at']}  {r['action']} by {r['reviewer']}{note}")


def _review_command(args, fn, label: str) -> None:
    console = _console()
    changed = fn(args.db, args.object, reviewer=getattr(args, "reviewer", "cli"),
                 note=getattr(args, "note", "") or "")
    if changed:
        console.print(f"{args.object} -> {label}")
    else:
        console.print(f"No knowledge object with id {args.object!r}.")


def _cmd_assign_owner(args) -> None:
    console = _console()
    try:
        changed = assign_owner(
            args.db, args.object, args.owner_type, args.owner_id,
            assigned_by=getattr(args, "reviewer", "cli"),
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return
    if changed:
        console.print(f"{args.object} owned by {args.owner_type}: {args.owner_id}")
    else:
        console.print(f"No knowledge object with id {args.object!r}.")


def _cmd_owners(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.all_owners(conn)
        names = {
            r["id"]: r["canonical_name"]
            for r in conn.execute("SELECT id, canonical_name FROM knowledge_objects")
        }
    console.print(f"[bold]Ownership[/bold] ({len(rows)} assigned):")
    if not rows:
        console.print("  (none)")
    for r in rows:
        label = names.get(r["object_id"], r["object_id"])
        console.print(f"  {r['owner_type']} / {r['owner_id']}  ->  {label}  (id={r['object_id']})")


def _cmd_domains(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = domain_analysis.domain_health(conn)
    console.print("[bold]Domain governance[/bold]")
    for d in rows:
        console.print(f"\n[bold]{d['domain']}[/bold]")
        console.print(
            f"  objects: {d['object_count']}    quality: {d['avg_quality']}    "
            f"freshness: {d['avg_freshness']}    review backlog: {d['review_backlog']}"
        )


def _cmd_drift(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.drift_findings(conn, args.limit)
    console.print(f"[bold]Knowledge drift[/bold] ({len(rows)}):")
    if not rows:
        console.print("  (none detected - run: catalog governance scan)")
    for r in rows:
        console.print(f"  [{r['field']}] {r['detail']}")


def _cmd_changes(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.recent_changes(conn, args.limit)
    console.print(f"[bold]Recent changes[/bold] ({len(rows)}):")
    if not rows:
        console.print("  (none)")
    for r in rows:
        change = f"{r['old_value']} -> {r['new_value']}" if r["new_value"] or r["old_value"] else ""
        console.print(
            f"  {r['detected_at']}  {r['change_type']}  {r['object_id'] or ''}  "
            f"{change}  {r['detail']}".rstrip()
        )


def _cmd_agent_approve(args) -> None:
    console = _console()
    policy = _config(args).agent_review
    overrides: dict = {}
    if args.agent is not None:
        overrides["agent_name"] = args.agent
    if args.min_confidence is not None:
        overrides["min_confidence"] = args.min_confidence
    if args.max_confidence is not None:
        overrides["max_confidence"] = args.max_confidence
    if overrides:
        policy = dataclasses.replace(policy, **overrides)

    stats = agent_approve(
        args.db,
        config=policy,
        target=args.target,
        note=args.note or "",
        dry_run=args.dry_run,
    )
    obj_n = sum(1 for c in stats.candidates if c["kind"] == "object")
    rel_n = sum(1 for c in stats.candidates if c["kind"] == "relationship")
    console.print(f"[bold]Agent review[/bold] (reviewer {stats.reviewer})")
    if stats.dry_run:
        console.print(
            f"Would approve: {obj_n} object(s), {rel_n} relationship(s)  "
            f"(dry run — nothing written)"
        )
    else:
        console.print(
            f"Approved: {stats.objects_approved} object(s), "
            f"{stats.relationships_approved} relationship(s)    "
            f"skipped: {stats.objects_skipped + stats.relationships_skipped}"
        )
    for c in stats.candidates[:25]:
        console.print(f"  [{c['kind']}] {c['label']}  (conf {c['confidence']:.2f})  id={c['id']}")


def _infer_target_kind(target: str) -> str:
    """Relationship ids are integers; object ids are slugs."""

    return "relationship" if str(target).isdigit() else "object"


def _cmd_revert(args) -> None:
    console = _console()
    kind = args.kind or _infer_target_kind(args.target)
    result = revert_review(
        args.db, kind, args.target, reviewer=args.reviewer, note=args.note or ""
    )
    if result.reverted:
        console.print(
            f"{result.target_kind} {result.target_id}: "
            f"{result.from_state} -> {result.to_state}"
        )
    else:
        console.print(f"[red]Nothing reverted: {result.reason}[/red]")


def _cmd_revert_agent(args) -> None:
    console = _console()
    stats = revert_agent_actions(
        args.db,
        agent=args.agent,
        since=args.since,
        reviewer=args.reviewer,
        note=args.note or "",
    )
    console.print(
        f"[bold]Revert agent actions[/bold]: {stats.reverted} reverted, "
        f"{stats.skipped} skipped"
    )
    for r in stats.results[:25]:
        if r.reverted:
            console.print(
                f"  {r.target_kind} {r.target_id}: {r.from_state} -> {r.to_state}"
            )


def _cmd_export(args) -> None:
    console = _console()
    config = _config(args)
    init_db(args.db)
    with connect(args.db) as conn:
        paths = export_mod.export_governance(conn, config, args.out)
    console.print("[bold]Governance reports written[/bold]:")
    for path in paths.values():
        console.print(f"  {path}")


def _cmd_ingest(args) -> None:
    from ..extraction import extract_all
    from ..knowledge.service import consolidate
    from ..links import discover_links, load_link_config
    from ..rdf.export import export_rdf
    from ..scanner import scan
    from . import ingestion

    console = _console()
    config = _config(args)
    schedule = args.schedule or config.ingestion.schedule

    def step_scan():
        return scan(args.config, args.db, args.cache).as_dict()

    def step_extract():
        return extract_all(args.db, args.cache)

    def step_links():
        return discover_links(
            db_path=args.db, cache_dir=args.cache,
            config=load_link_config(args.link_config), artifact_id=None,
        )

    def step_consolidate():
        return consolidate(args.db).as_dict()

    def step_rdf():
        with connect(args.db) as conn:
            return {k: str(v) for k, v in export_rdf(conn).items()}

    def step_governance():
        return run_scan(args.db, config).as_dict()

    steps = [
        ("scan", step_scan),
        ("extract", step_extract),
        ("discover-links", step_links),
        ("consolidate", step_consolidate),
        ("rdf-export", step_rdf),
        ("governance-scan", step_governance),
    ]

    result = ingestion.run_ingestion(
        args.db, steps, schedule=schedule, force=args.force,
    )
    if not result.ran:
        console.print(f"Ingestion skipped: {result.skipped_reason}")
        return
    console.print(f"[bold]Ingestion run[/bold] (schedule: {result.schedule})")
    for s in result.steps:
        status = "OK  " if s.ok else "FAIL"
        console.print(f"  {status}  {s.name}")
    console.print("Done." if result.ok else "Completed with errors.")


# -- parser wiring ------------------------------------------------------------

def add_governance_parser(sub: argparse._SubParsersAction) -> None:
    """Register the ``governance`` command group on the top-level subparsers."""

    gov = sub.add_parser(
        "governance", help="govern the knowledge graph (Prompt #10)"
    )
    gov.add_argument(
        "--out", default=export_mod.DEFAULT_OUT_DIR,
        help="output directory for governance exports",
    )
    gsub = gov.add_subparsers(dest="governance_command", required=True)

    gsub.add_parser("scan", help="run a governance scan (lifecycle, drift, quality, alerts)")
    gsub.add_parser("dashboard", help="knowledge health dashboard")
    gsub.add_parser("review-queue", help="objects awaiting review")
    gsub.add_parser("stale", help="stale / aging knowledge")

    q = gsub.add_parser("quality", help="quality scores")
    q.add_argument("--limit", type=int, default=30)
    q.add_argument("--ascending", action="store_true", help="lowest quality first")

    gsub.add_parser("orphaned", help="orphan detection")

    al = gsub.add_parser("alerts", help="open governance alerts")
    al.add_argument("--type", default=None, help="filter by alert type")
    al.add_argument("--limit", type=int, default=50)

    hist = gsub.add_parser("history", help="full history of one object")
    hist.add_argument("object")

    for name, help_text in (
        ("approve", "approve an object (trusted, exported)"),
        ("archive", "archive an object (retired, kept for history)"),
        ("reject", "reject an object (not trusted)"),
        ("flag", "flag an object as needing attention"),
    ):
        p = gsub.add_parser(name, help=help_text)
        p.add_argument("object")
        p.add_argument("--reviewer", default="cli")
        p.add_argument("--note", default="")

    aa = gsub.add_parser(
        "agent-approve",
        help="let an agent approve eligible PROPOSED items under the policy",
    )
    aa.add_argument(
        "--target", choices=("objects", "relationships", "all"), default="all"
    )
    aa.add_argument("--agent", default=None, help="agent name (provenance: agent:<name>)")
    aa.add_argument("--min-confidence", type=float, default=None, dest="min_confidence")
    aa.add_argument("--max-confidence", type=float, default=None, dest="max_confidence")
    aa.add_argument("--note", default="")
    aa.add_argument(
        "--dry-run", action="store_true", help="list what would be approved; write nothing"
    )

    rv = gsub.add_parser("revert", help="undo the latest review decision on one target")
    rv.add_argument("target", help="object id or relationship id")
    rv.add_argument(
        "--kind", choices=("object", "relationship"), default=None,
        help="override the inferred target kind",
    )
    rv.add_argument("--reviewer", default="cli")
    rv.add_argument("--note", default="")

    ra = gsub.add_parser(
        "revert-agent", help="bulk-undo agent decisions (by agent name / since)"
    )
    ra.add_argument("--agent", default=None, help="limit to this agent (default: all agents)")
    ra.add_argument("--since", default=None, help="ISO-8601 lower bound on decision time")
    ra.add_argument("--reviewer", default="cli")
    ra.add_argument("--note", default="")

    owner = gsub.add_parser("assign-owner", help="assign an owner to an object")
    owner.add_argument("object")
    owner.add_argument("owner_type", help="Team | Person | Domain")
    owner.add_argument("owner_id", help="e.g. 'Test & Release Team'")
    owner.add_argument("--reviewer", default="cli")

    gsub.add_parser("owners", help="list ownership assignments")
    gsub.add_parser("domains", help="per-domain governance health")

    dr = gsub.add_parser("drift", help="detected knowledge drift")
    dr.add_argument("--limit", type=int, default=30)

    ch = gsub.add_parser("changes", help="recent changes (audit trail)")
    ch.add_argument("--limit", type=int, default=30)

    gsub.add_parser("export", help="write the four governance JSON reports")

    ing = gsub.add_parser("ingest", help="run the ingestion pipeline on a schedule")
    ing.add_argument("--schedule", default=None, help="daily | weekly | manual")
    ing.add_argument("--force", action="store_true", help="run even if not due")


def run_governance(args) -> None:
    """Dispatch a parsed ``governance`` subcommand."""

    handlers = {
        "scan": _cmd_scan,
        "dashboard": _cmd_dashboard,
        "review-queue": _cmd_review_queue,
        "stale": _cmd_stale,
        "quality": _cmd_quality,
        "orphaned": _cmd_orphaned,
        "alerts": _cmd_alerts,
        "history": _cmd_history,
        "assign-owner": _cmd_assign_owner,
        "owners": _cmd_owners,
        "domains": _cmd_domains,
        "drift": _cmd_drift,
        "changes": _cmd_changes,
        "agent-approve": _cmd_agent_approve,
        "revert": _cmd_revert,
        "revert-agent": _cmd_revert_agent,
        "export": _cmd_export,
        "ingest": _cmd_ingest,
    }
    command = args.governance_command
    if command in handlers:
        handlers[command](args)
    elif command == "approve":
        _review_command(args, approve_object, "APPROVED")
    elif command == "archive":
        _review_command(args, archive_object, "ARCHIVED")
    elif command == "reject":
        _review_command(args, reject_object, "REJECTED")
    elif command == "flag":
        _review_command(args, flag_object, "NEEDS_ATTENTION")


__all__ = ["add_governance_parser", "run_governance"]
