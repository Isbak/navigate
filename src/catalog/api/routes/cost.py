"""Cost endpoints: LLM token usage and spend analytics.

A read-only projection of the ``llm_usage`` ledger that backs the ``cost-report``
CLI. No external/LLM calls are made - these endpoints only summarise what has
already been recorded, so they are always safe to expose.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from ...cost import repository as cost_repo
from .. import serializers
from ..dependencies import get_db
from ..schemas import (
    CostByModel,
    CostByOperation,
    CostPerDocument,
    CostSummary,
    CostVsQuality,
)

router = APIRouter(prefix="/cost", tags=["cost"])


@router.get("/summary", response_model=CostSummary)
def summary(conn: sqlite3.Connection = Depends(get_db)) -> CostSummary:
    """Overall token usage and spend across every recorded LLM call."""

    return serializers.cost_summary(cost_repo.totals(conn))


@router.get("/by-operation", response_model=list[CostByOperation])
def by_operation(conn: sqlite3.Connection = Depends(get_db)) -> list[CostByOperation]:
    return [serializers.cost_by_operation(r) for r in cost_repo.by_operation(conn)]


@router.get("/by-model", response_model=list[CostByModel])
def by_model(conn: sqlite3.Connection = Depends(get_db)) -> list[CostByModel]:
    return [serializers.cost_by_model(r) for r in cost_repo.by_model(conn)]


@router.get("/per-document", response_model=list[CostPerDocument])
def per_document(
    conn: sqlite3.Connection = Depends(get_db),
    top: int = Query(20, ge=1, le=1000, description="return the N most expensive documents"),
) -> list[CostPerDocument]:
    return [
        serializers.cost_per_document(r) for r in cost_repo.cost_per_document(conn, top)
    ]


@router.get("/vs-quality", response_model=list[CostVsQuality])
def vs_quality(
    conn: sqlite3.Connection = Depends(get_db),
    top: int = Query(20, ge=1, le=1000, description="return the N most expensive documents"),
) -> list[CostVsQuality]:
    """Per-document spend beside the model's own classification confidence."""

    return [
        serializers.cost_vs_quality(r) for r in cost_repo.cost_vs_quality(conn, top)
    ]
