"""Enrich the compliance metadata tables from the consolidated graph.

Consolidation creates the ``Standard`` / ``Requirement`` knowledge objects and
the ``mandated_by`` edges between them, but the generic object model cannot carry
clause locators, versions, or effective dates. ``sync_requirements`` walks the
``candidate_requirements`` rows, recomputes each requirement's stable object id
the same way consolidation did, and - only when that object actually exists -
upserts the enriched ``compliance_requirements`` / ``compliance_standards`` rows.

It is idempotent and safe to run before every ``assess``; it never creates
knowledge objects (that is consolidation's job) and never deletes assessments.
"""

from __future__ import annotations

import sqlite3

from ..knowledge.ids import object_id, requirement_display_name
from . import repository as repo


def sync_requirements(conn: sqlite3.Connection, now: str) -> int:
    """Populate compliance metadata from candidate requirements. Returns count."""

    existing = repo.existing_object_ids(conn)
    rows = conn.execute(
        """
        SELECT standard_name, standard_version, clause_ref, title,
               requirement_text, obligation_level, confidence
        FROM candidate_requirements
        WHERE review_status != 'REJECTED'
        ORDER BY confidence DESC
        """
    ).fetchall()

    synced = 0
    seen_requirements: set[str] = set()
    seen_standards: set[str] = set()
    for r in rows:
        standard_name = (r["standard_name"] or "").strip()
        req_name = requirement_display_name(
            standard_name, r["clause_ref"] or "", r["title"] or ""
        )
        req_id = object_id("Requirement", req_name)
        if req_id not in existing or req_id in seen_requirements:
            continue

        std_id = ""
        if standard_name:
            candidate_std = object_id("Standard", standard_name)
            if candidate_std in existing:
                std_id = candidate_std
                if std_id not in seen_standards:
                    repo.upsert_standard(
                        conn,
                        object_id=std_id,
                        name=standard_name,
                        authority="",
                        version=r["standard_version"] or "",
                        jurisdiction="",
                        effective_from="",
                        source_url="",
                        now=now,
                    )
                    seen_standards.add(std_id)

        repo.upsert_requirement(
            conn,
            object_id=req_id,
            standard_object_id=std_id,
            clause_ref=r["clause_ref"] or "",
            title=r["title"] or "",
            requirement_text=r["requirement_text"] or "",
            obligation_level=(r["obligation_level"] or "MANDATORY"),
            assessed_against_version=r["standard_version"] or "",
            now=now,
        )
        seen_requirements.add(req_id)
        synced += 1

    return synced


__all__ = ["sync_requirements"]
