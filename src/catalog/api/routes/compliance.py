"""Compliance endpoints: standards, requirements, coverage, gaps, assessments,
the prove-compliance proof, and an assess job trigger.

Read endpoints reflect the *trusted* posture - coverage and gaps count a
requirement as met only once a human has APPROVED its assessment - matching the
CLI. ``POST /assess`` runs the assessment engine as a tracked background job.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query

from ...compliance import repository as comp_repo
from ...compliance.service import prove as prove_service
from ...compliance.service import review_assessment
from ...jobs.service import JobContext, run_job
from .. import serializers
from ..dependencies import build_job_context, get_db
from ..errors import not_found
from ..pagination import Pagination, pagination_params
from ..schemas import (
    ActionResponse,
    ComplianceAssessment,
    ComplianceCoverageResponse,
    ComplianceEquation,
    ComplianceEquationVariable,
    ComplianceGap,
    ComplianceProofResponse,
    ComplianceRequirement,
    ComplianceStandard,
    Job,
    PaginatedResponse,
)

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.get("/standards", response_model=list[ComplianceStandard])
def standards(conn: sqlite3.Connection = Depends(get_db)) -> list[ComplianceStandard]:
    return [
        ComplianceStandard(
            object_id=r["object_id"],
            name=r["name"] or r["object_name"] or r["object_id"],
            authority=r["authority"] or "",
            version=r["version"] or "",
            jurisdiction=r["jurisdiction"] or "",
            status=r["object_status"],
        )
        for r in comp_repo.standards(conn)
    ]


@router.get("/standards/{object_id}", response_model=ComplianceStandard)
def standard(
    object_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> ComplianceStandard:
    r = comp_repo.get_standard(conn, object_id)
    if r is None:
        raise not_found("Standard not found", object_id=object_id)
    return ComplianceStandard(
        object_id=r["object_id"],
        name=r["name"] or r["object_id"],
        authority=r["authority"] or "",
        version=r["version"] or "",
        jurisdiction=r["jurisdiction"] or "",
    )


@router.get("/requirements", response_model=PaginatedResponse[ComplianceRequirement])
def requirements(
    conn: sqlite3.Connection = Depends(get_db),
    page: Pagination = Depends(pagination_params),
    standard: str | None = Query(None, description="filter by standard object id"),
) -> PaginatedResponse[ComplianceRequirement]:
    rows = comp_repo.requirements(conn, standard)
    total = len(rows)
    window = rows[page.offset : page.offset + page.limit]
    items = [
        ComplianceRequirement(
            object_id=r["object_id"],
            name=r["object_name"] or r["object_id"],
            standard_object_id=r["standard_object_id"] or "",
            clause_ref=r["clause_ref"] or "",
            title=r["title"] or "",
            requirement_text=r["requirement_text"] or "",
            obligation_level=r["obligation_level"] or "",
            status=r["object_status"],
        )
        for r in window
    ]
    return PaginatedResponse(items=items, limit=page.limit, offset=page.offset, total=total)


@router.get("/requirements/{object_id}", response_model=ComplianceRequirement)
def requirement(
    object_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> ComplianceRequirement:
    r = comp_repo.get_requirement(conn, object_id)
    if r is None:
        raise not_found("Requirement not found", object_id=object_id)
    return ComplianceRequirement(
        object_id=r["object_id"],
        name=r["object_name"] or r["object_id"],
        standard_object_id=r["standard_object_id"] or "",
        clause_ref=r["clause_ref"] or "",
        title=r["title"] or "",
        requirement_text=r["requirement_text"] or "",
        obligation_level=r["obligation_level"] or "",
        status=r["object_status"],
    )


def _equation(r: sqlite3.Row) -> ComplianceEquation:
    try:
        raw_vars = json.loads(r["variables"] or "[]")
    except (TypeError, ValueError):
        raw_vars = []
    variables = [
        ComplianceEquationVariable(
            symbol=str(v.get("symbol", "")),
            description=str(v.get("description", "")),
            unit=str(v.get("unit", "")),
        )
        for v in raw_vars
        if isinstance(v, dict) and v.get("symbol")
    ]
    return ComplianceEquation(
        object_id=r["object_id"],
        name=r["object_name"] or r["symbol"] or r["object_id"],
        standard_object_id=r["standard_object_id"] or "",
        requirement_object_id=r["requirement_object_id"] or "",
        clause_ref=r["clause_ref"] or "",
        symbol=r["symbol"] or "",
        title=r["title"] or "",
        expression=r["expression"] or "",
        python_code=r["python_code"] or "",
        ast_json=r["ast_json"] or "",
        variables=variables,
        latex=r["latex"] or "",
        valid=bool(r["valid"]),
        validation_note=r["validation_note"] or "",
        status=r["object_status"],
    )


@router.get("/equations", response_model=PaginatedResponse[ComplianceEquation])
def equations(
    conn: sqlite3.Connection = Depends(get_db),
    page: Pagination = Depends(pagination_params),
    standard: str | None = Query(None, description="filter by standard object id"),
) -> PaginatedResponse[ComplianceEquation]:
    rows = comp_repo.equations(conn, standard)
    total = len(rows)
    window = rows[page.offset : page.offset + page.limit]
    items = [_equation(r) for r in window]
    return PaginatedResponse(items=items, limit=page.limit, offset=page.offset, total=total)


@router.get("/equations/{object_id}", response_model=ComplianceEquation)
def equation(
    object_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> ComplianceEquation:
    r = comp_repo.get_equation(conn, object_id)
    if r is None:
        raise not_found("Equation not found", object_id=object_id)
    return _equation(r)


@router.get("/coverage", response_model=ComplianceCoverageResponse)
def coverage(conn: sqlite3.Connection = Depends(get_db)) -> ComplianceCoverageResponse:
    # coverage_service opens its own connection; reuse the request connection's
    # path via the analytics helpers instead to stay within this transaction.
    rows = comp_repo.coverage_by_standard(conn)
    by_standard = []
    total_all = satisfied_all = 0
    for r in rows:
        total = r["total"] or 0
        satisfied = r["satisfied"] or 0
        total_all += total
        satisfied_all += satisfied
        by_standard.append(
            {
                "standard_object_id": r["standard_object_id"] or "",
                "standard_name": r["standard_name"]
                or r["standard_object_id"]
                or "(unattributed)",
                "total": total,
                "satisfied": satisfied,
                "partial": r["partial"] or 0,
                "coverage": round(satisfied / total, 4) if total else 0.0,
            }
        )
    overall = round(satisfied_all / total_all, 4) if total_all else 0.0
    return ComplianceCoverageResponse(overall=overall, standards=by_standard)


@router.get("/gaps", response_model=list[ComplianceGap])
def gaps(conn: sqlite3.Connection = Depends(get_db)) -> list[ComplianceGap]:
    return [
        ComplianceGap(
            object_id=r["object_id"],
            requirement_name=r["requirement_name"] or r["object_id"],
            clause_ref=r["clause_ref"] or "",
            title=r["title"] or "",
            obligation_level=r["obligation_level"] or "",
            standard_object_id=r["standard_object_id"] or "",
            standard_name=r["standard_name"] or r["standard_object_id"] or "",
        )
        for r in comp_repo.open_gaps(conn)
    ]


@router.get("/assessments", response_model=list[ComplianceAssessment])
def assessments(
    conn: sqlite3.Connection = Depends(get_db),
    status: str | None = Query(None),
) -> list[ComplianceAssessment]:
    return [
        ComplianceAssessment(
            id=a["id"],
            requirement_object_id=a["requirement_object_id"],
            requirement_name=a["requirement_name"],
            control_object_id=a["control_object_id"],
            control_name=a["control_name"],
            status=a["status"],
            review_status=a["review_status"],
            assessed_against_version=a["assessed_against_version"] or "",
            rationale=a["rationale"] or "",
        )
        for a in comp_repo.assessments(conn, status)
    ]


@router.get("/prove/{requirement}", response_model=ComplianceProofResponse)
def prove(
    requirement: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    return prove_service(conn_path(conn), requirement)


@router.post("/assessments/{assessment_id}/approve", response_model=ActionResponse)
def approve(
    assessment_id: int, conn: sqlite3.Connection = Depends(get_db)
) -> ActionResponse:
    return _review(conn, assessment_id, "APPROVED")


@router.post("/assessments/{assessment_id}/reject", response_model=ActionResponse)
def reject(
    assessment_id: int, conn: sqlite3.Connection = Depends(get_db)
) -> ActionResponse:
    return _review(conn, assessment_id, "REJECTED")


@router.post("/assess", response_model=Job)
def assess(ctx: JobContext = Depends(build_job_context)) -> Job:
    """Run the compliance assessment engine as a tracked background job."""

    return serializers.job(run_job(ctx, "compliance-assess"))


# -- helpers ------------------------------------------------------------------

def conn_path(conn: sqlite3.Connection) -> str:
    """Return the file path backing a SQLite connection (for service calls)."""

    for _, name, filename in conn.execute("PRAGMA database_list"):
        if name == "main" and filename:
            return filename
    return "data/catalog.sqlite"


def _review(
    conn: sqlite3.Connection, assessment_id: int, review_status: str
) -> ActionResponse:
    if comp_repo.get_assessment(conn, assessment_id) is None:
        raise not_found("Assessment not found", assessment_id=assessment_id)
    review_assessment(conn_path(conn), assessment_id, review_status, reviewer="api")
    return ActionResponse(
        id=str(assessment_id),
        status=review_status,
        message=f"Assessment {assessment_id} {review_status.lower()}.",
    )
