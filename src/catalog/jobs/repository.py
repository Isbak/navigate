"""Persistence for the job tracker.

Owns every SQL statement touching the ``jobs`` table. The service layer hands
this module already-decided state transitions; nothing here runs a pipeline.
"""

from __future__ import annotations

import json
import sqlite3

from .models import JobStatus


def create_job(conn: sqlite3.Connection, *, job_type: str, created_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO jobs(job_type, status, created_at) VALUES (?, ?, ?)",
        (job_type, JobStatus.PENDING.value, created_at),
    )
    return int(cur.lastrowid)


def mark_running(conn: sqlite3.Connection, job_id: int, *, started_at: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, started_at = ? WHERE id = ?",
        (JobStatus.RUNNING.value, started_at, job_id),
    )


def mark_completed(
    conn: sqlite3.Connection, job_id: int, *, completed_at: str, result_summary: dict
) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, completed_at = ?, result_summary = ? WHERE id = ?",
        (
            JobStatus.COMPLETED.value,
            completed_at,
            json.dumps(result_summary),
            job_id,
        ),
    )


def mark_failed(
    conn: sqlite3.Connection, job_id: int, *, completed_at: str, error_message: str
) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, completed_at = ?, error_message = ? WHERE id = ?",
        (JobStatus.FAILED.value, completed_at, error_message, job_id),
    )


def get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs(
    conn: sqlite3.Connection,
    *,
    job_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    where: list[str] = []
    params: list[object] = []
    if job_type:
        where.append("job_type = ?")
        params.append(job_type)
    if status:
        where.append("status = ?")
        params.append(status)
    clause = f" WHERE {' AND '.join(where)}" if where else ""

    total = int(
        conn.execute(f"SELECT COUNT(*) FROM jobs{clause}", params).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT * FROM jobs{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return rows, total


__all__ = [
    "create_job",
    "mark_running",
    "mark_completed",
    "mark_failed",
    "get_job",
    "list_jobs",
]
