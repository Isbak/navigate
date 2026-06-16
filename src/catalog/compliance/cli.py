"""CLI surface for the compliance layer (``catalog compliance ...``).

Wires the compliance engine into the command line, rendering with Rich to match
the ``catalog governance`` command group. The typical flow is::

    catalog compliance import config/standards/iso27001.yml
    catalog consolidate
    catalog compliance assess
    catalog compliance coverage     # trusted coverage (approved claims only)
    catalog compliance gaps
    catalog compliance prove "GDPR Art. 32"

The engine only *proposes* an assessment status; ``coverage``/``gaps``/``prove``
count a requirement as met only once a human has APPROVED its assessment.
"""

from __future__ import annotations

import argparse
import json

from ..db import connect, init_db
from . import repository as repo
from .config import load_compliance_config
from .importer import import_standard
from .models import AssessmentStatus, ComplianceReviewState
from .service import assess, coverage, gaps, prove, review_assessment


def _console():
    from rich.console import Console

    return Console(width=120, highlight=False)


def _config(args):
    return load_compliance_config(
        getattr(args, "compliance_config", "config/compliance.yml")
    )


# -- command handlers ---------------------------------------------------------

def _cmd_import(args) -> None:
    console = _console()
    stats = import_standard(args.db, args.path)
    console.print("[bold]Curated import complete[/bold]")
    console.print(
        f"Standard: {stats.standard_name or '(unnamed)'} "
        f"{stats.standard_version}".rstrip()
    )
    console.print(f"Requirements imported: {stats.requirements_imported}")
    if stats.equations_imported:
        console.print(f"Equations imported: {stats.equations_imported}")
    console.print(
        "Run [bold]catalog consolidate[/bold] then "
        "[bold]catalog compliance assess[/bold] to evaluate them."
    )


def _cmd_assess(args) -> None:
    console = _console()
    stats = assess(args.db, _config(args))
    d = stats.as_dict()
    console.print("[bold]Compliance assessment complete[/bold]")
    console.print(f"Requirements assessed: {d['requirements_assessed']}")
    console.print(
        f"Satisfied: {d['satisfied']}    Partial: {d['partial']}    "
        f"Gaps: {d['gaps']}"
    )
    console.print(f"Derived coverage: {d['coverage'] * 100:.1f}% (before human approval)")
    console.print(
        "Approve assessments with "
        "[bold]catalog compliance approve <id>[/bold] to count them as covered."
    )


def _cmd_standards(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.standards(conn)
    console.print(f"[bold]Standards[/bold] ({len(rows)}):")
    if not rows:
        console.print("  (none - import a catalog or classify a standard document)")
    for r in rows:
        version = f" {r['version']}" if r["version"] else ""
        console.print(
            f"  {r['name'] or r['object_id']}{version}  "
            f"[{r['object_status'] or '?'}]  id={r['object_id']}"
        )


def _cmd_requirements(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.requirements(conn, getattr(args, "standard", None))
    console.print(f"[bold]Requirements[/bold] ({len(rows)}):")
    if not rows:
        console.print("  (none)")
    for r in rows:
        clause = r["clause_ref"] or ""
        console.print(
            f"  [{r['obligation_level'] or '?'}] {clause}  "
            f"{r['title'] or r['object_name'] or r['object_id']}  id={r['object_id']}"
        )


def _cmd_equations(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        rows = repo.equations(conn, getattr(args, "standard", None))
    console.print(f"[bold]Equations[/bold] ({len(rows)}):")
    if not rows:
        console.print("  (none - import a catalog with equations or classify a standard)")
    for r in rows:
        clause = r["clause_ref"] or ""
        flag = "" if r["valid"] else " [INVALID]"
        console.print(
            f"  {r['symbol'] or '?'}{flag}  {clause}  "
            f"{r['title'] or r['object_name'] or ''}  "
            f"[{r['object_status'] or '?'}]  id={r['object_id']}"
        )


def _cmd_show_equation(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        eq_id = repo.find_equation_id(conn, args.equation)
        if eq_id is None:
            console.print(f"No equation matches '{args.equation}'.")
            return
        eq = repo.get_equation(conn, eq_id)
    console.print(
        f"[bold]{eq['object_name'] or eq['symbol'] or eq['object_id']}[/bold] "
        f"({eq['clause_ref'] or 'no clause ref'})  [{eq['object_status'] or '?'}]"
    )
    if eq["title"]:
        console.print(f"  title:      {eq['title']}")
    console.print(f"  standard:   {eq['standard_object_id'] or '(unattributed)'}")
    if eq["requirement_object_id"]:
        console.print(f"  specifies:  {eq['requirement_object_id']}")
    if eq["valid"]:
        console.print("  valid:      yes")
    else:
        console.print(f"  valid:      no ({eq['validation_note'] or 'not validated'})")
    if eq["latex"]:
        console.print(f"  notation:   {eq['latex']}")
    if eq["expression"]:
        console.print(f"  expression: {eq['expression']}")
    try:
        variables = json.loads(eq["variables"] or "[]")
    except (TypeError, ValueError):
        variables = []
    if variables:
        console.print("  variables:")
        for v in variables:
            unit = f" [{v.get('unit')}]" if v.get("unit") else ""
            desc = f" - {v.get('description')}" if v.get("description") else ""
            console.print(f"    {v.get('symbol', '?')}{unit}{desc}")
    if eq["python_code"]:
        console.print("  python:")
        for line in eq["python_code"].splitlines():
            console.print(f"    {line}")


def _cmd_coverage(args) -> None:
    console = _console()
    data = coverage(args.db)
    console.print(
        f"[bold]Compliance coverage[/bold] (approved claims only): "
        f"{data['overall'] * 100:.1f}% overall"
    )
    if not data["standards"]:
        console.print("  (no requirements - assess first)")
    for s in data["standards"]:
        console.print(
            f"  {s['standard_name']}: {s['satisfied']}/{s['total']} satisfied "
            f"({s['coverage'] * 100:.1f}%), {s['partial']} partial"
        )


def _cmd_gaps(args) -> None:
    console = _console()
    rows = gaps(args.db)
    console.print(f"[bold]Open compliance gaps[/bold] ({len(rows)}):")
    if not rows:
        console.print("  (none - every requirement has an approved satisfying control)")
    for g in rows:
        clause = g["clause_ref"] or ""
        console.print(
            f"  [{g['obligation_level'] or '?'}] {g['standard_name']} {clause}  "
            f"{g['title'] or g['requirement_name']}  id={g['object_id']}"
        )


def _cmd_assessments(args) -> None:
    console = _console()
    init_db(args.db)
    status = getattr(args, "status", None)
    with connect(args.db) as conn:
        rows = repo.assessments(conn, status)
    console.print(f"[bold]Assessments[/bold] ({len(rows)}):")
    if not rows:
        console.print("  (none)")
    for a in rows:
        control = a["control_name"] or a["control_object_id"] or "-"
        console.print(
            f"  #{a['id']} [{a['status']}/{a['review_status']}] "
            f"{a['requirement_name'] or a['requirement_object_id']} <- {control}"
        )


def _cmd_show(args) -> None:
    console = _console()
    init_db(args.db)
    with connect(args.db) as conn:
        req_id = repo.find_requirement_id(conn, args.requirement)
        if req_id is None:
            console.print(f"No requirement matches '{args.requirement}'.")
            return
        req = repo.get_requirement(conn, req_id)
        assessments = [
            a for a in repo.assessments(conn) if a["requirement_object_id"] == req_id
        ]
    console.print(
        f"[bold]{req['object_name'] or req['object_id']}[/bold] "
        f"({req['clause_ref'] or 'no clause ref'})"
    )
    console.print(f"  obligation: {req['obligation_level']}")
    console.print(f"  standard:   {req['standard_object_id'] or '(unattributed)'}")
    if req["requirement_text"]:
        console.print(f"  text:       {req['requirement_text']}")
    console.print(f"  assessments ({len(assessments)}):")
    for a in assessments:
        control = a["control_name"] or a["control_object_id"] or "-"
        console.print(
            f"    #{a['id']} [{a['status']}/{a['review_status']}] {control}: "
            f"{a['rationale']}"
        )


def _cmd_prove(args) -> None:
    console = _console()
    result = prove(args.db, args.requirement)
    if not result["found"]:
        console.print(result["message"])
        return
    req = result["requirement"]
    console.print(
        f"[bold]Prove compliance:[/bold] {req.get('name', req['object_id'])} "
        f"({req.get('clause_ref', '')})"
    )
    if not result["proven"]:
        # The platform's standard decline rather than a fabricated conclusion.
        console.print(f"  {result['message']}")
        return
    for a in result["assessments"]:
        console.print(
            f"  [{a['status']}] satisfied by {a['control_name'] or a['control_object_id']} "
            f"(assessed against {a['assessed_against_version'] or 'n/a'})"
        )
        console.print(f"    {a['rationale']}")
        for e in a["evidence"]:
            clause = f" [{e['clause_ref']}]" if e["clause_ref"] else ""
            console.print(f"      - \"{e['quote']}\"{clause}  ({e['artifact_id']})")


def _cmd_review(args, review_status: str) -> None:
    console = _console()
    changed = review_assessment(
        args.db, args.assessment_id, review_status,
        reviewer=getattr(args, "reviewer", "cli"), note=getattr(args, "note", ""),
    )
    if changed:
        console.print(f"Assessment #{args.assessment_id} -> {review_status}")
    else:
        console.print(f"Assessment #{args.assessment_id} not found.")


# -- parser wiring ------------------------------------------------------------

def add_compliance_parser(sub: argparse._SubParsersAction) -> None:
    """Register the ``compliance`` command group on the top-level subparsers."""

    comp = sub.add_parser(
        "compliance", help="map controls to standards and assess compliance"
    )
    csub = comp.add_subparsers(dest="compliance_command", required=True)

    imp = csub.add_parser("import", help="import a curated standard catalog (YAML/CSV)")
    imp.add_argument("path", help="path to the framework catalog file")

    csub.add_parser("assess", help="assess every requirement against its controls")
    csub.add_parser("standards", help="list standards")

    reqs = csub.add_parser("requirements", help="list requirements")
    reqs.add_argument("--standard", default=None, help="filter by standard object id")

    eqs = csub.add_parser("equations", help="list extracted equations")
    eqs.add_argument("--standard", default=None, help="filter by standard object id")

    showeq = csub.add_parser(
        "show-equation", help="show one equation (formula, AST, Python, variables)"
    )
    showeq.add_argument("equation", help="object id, symbol, clause ref, or name fragment")

    csub.add_parser("coverage", help="per-standard coverage (approved claims only)")
    csub.add_parser("gaps", help="requirements with no approved satisfying control")

    ass = csub.add_parser("assessments", help="list assessments")
    ass.add_argument(
        "--status", default=None,
        choices=[s.value for s in AssessmentStatus],
        help="filter by assessment status",
    )

    show = csub.add_parser("show", help="show one requirement and its assessments")
    show.add_argument("requirement", help="object id, clause ref, or name fragment")

    pr = csub.add_parser("prove", help="prove compliance with one requirement")
    pr.add_argument("requirement", help="object id, clause ref, or name fragment")

    for name, help_text in (
        ("approve", "approve a compliance assessment (counts toward coverage)"),
        ("reject", "reject a compliance assessment"),
    ):
        p = csub.add_parser(name, help=help_text)
        p.add_argument("assessment_id", type=int)
        p.add_argument("--reviewer", default="cli")
        p.add_argument("--note", default="")


def run_compliance(args) -> None:
    """Dispatch a parsed ``compliance`` subcommand."""

    handlers = {
        "import": _cmd_import,
        "assess": _cmd_assess,
        "standards": _cmd_standards,
        "requirements": _cmd_requirements,
        "equations": _cmd_equations,
        "show-equation": _cmd_show_equation,
        "coverage": _cmd_coverage,
        "gaps": _cmd_gaps,
        "assessments": _cmd_assessments,
        "show": _cmd_show,
        "prove": _cmd_prove,
    }
    command = args.compliance_command
    if command in handlers:
        handlers[command](args)
    elif command == "approve":
        _cmd_review(args, ComplianceReviewState.APPROVED.value)
    elif command == "reject":
        _cmd_review(args, ComplianceReviewState.REJECTED.value)


__all__ = ["add_compliance_parser", "run_compliance"]
