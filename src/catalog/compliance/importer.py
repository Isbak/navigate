"""Curated framework import: load a standard's clauses into candidate requirements.

Well-known frameworks (ISO 27001, GDPR, NIS2, ...) are better entered from a
maintained catalog than mined from a PDF. This module reads a YAML or CSV catalog
- a standard's metadata plus a list of requirement rows - and writes them into
``candidate_requirements`` with ``model='curated_import'`` and a high confidence,
so the curated and LLM-mined paths *converge*: a subsequent ``consolidate`` turns
both into the same ``Standard`` / ``Requirement`` knowledge objects.

YAML shape::

    standard:
      name: ISO 27001
      version: "2022"
      authority: ISO
      jurisdiction: International
    requirements:
      - clause_ref: A.5.1
        title: Policies for information security
        text: Information security policy ... shall be defined.
        obligation_level: MANDATORY

CSV shape: a header row with columns ``clause_ref,title,text,obligation_level``
and optional ``standard_name,standard_version``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..db import connect, init_db
from ..knowledge.ids import slugify

# Curated rows are trusted (a maintainer entered them), so they enter at high
# confidence; consolidation and human review still gate what becomes trusted.
_CURATED_CONFIDENCE = 0.95
_CURATED_MODEL = "curated_import"
_OBLIGATION_LEVELS = ("MANDATORY", "RECOMMENDED", "OPTIONAL")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ImportStats:
    standard_name: str = ""
    standard_version: str = ""
    requirements_imported: int = 0
    artifact_id: str = ""

    def as_dict(self) -> dict:
        return {
            "standard_name": self.standard_name,
            "standard_version": self.standard_version,
            "requirements_imported": self.requirements_imported,
            "artifact_id": self.artifact_id,
        }


def _normalize_obligation(value: object) -> str:
    text = str(value or "").strip().upper()
    return text if text in _OBLIGATION_LEVELS else "MANDATORY"


def _rows_from_yaml(data: dict) -> tuple[dict, list[dict]]:
    standard = data.get("standard") if isinstance(data.get("standard"), dict) else {}
    requirements = data.get("requirements")
    rows: list[dict] = []
    if isinstance(requirements, list):
        for item in requirements:
            if isinstance(item, dict):
                rows.append(item)
    return standard, rows


def _rows_from_csv(path: Path) -> tuple[dict, list[dict]]:
    rows: list[dict] = []
    standard: dict = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for item in csv.DictReader(fh):
            rows.append(item)
            if not standard and item.get("standard_name"):
                standard = {
                    "name": item.get("standard_name", ""),
                    "version": item.get("standard_version", ""),
                }
    return standard, rows


def load_catalog(path: str | Path) -> tuple[dict, list[dict]]:
    """Parse a catalog file into ``(standard_metadata, requirement_rows)``."""

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Catalog not found: {p}")
    if p.suffix.lower() in {".yml", ".yaml"}:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Catalog {p} must be a YAML mapping")
        return _rows_from_yaml(data)
    if p.suffix.lower() == ".csv":
        return _rows_from_csv(p)
    raise ValueError(f"Unsupported catalog format: {p.suffix} (use .yml or .csv)")


def import_standard(
    db_path: str | Path, catalog_path: str | Path
) -> ImportStats:
    """Import a curated standard catalog into ``candidate_requirements``.

    Re-importing the same standard replaces its prior curated rows (keyed by the
    synthetic ``import_<standard>`` artifact id) so the catalog stays the single
    source of truth for that framework.
    """

    standard, rows = load_catalog(catalog_path)
    standard_name = str(standard.get("name") or "").strip()
    standard_version = str(standard.get("version") or "").strip()
    artifact_id = f"import_{slugify(standard_name or Path(catalog_path).stem)}"
    now = _utc_now()
    stats = ImportStats(
        standard_name=standard_name,
        standard_version=standard_version,
        artifact_id=artifact_id,
    )

    init_db(db_path)
    with connect(db_path) as conn:
        # Replace any prior curated import for this standard.
        conn.execute(
            "DELETE FROM candidate_requirements WHERE artifact_id = ? AND model = ?",
            (artifact_id, _CURATED_MODEL),
        )
        records = []
        for row in rows:
            text = str(row.get("text") or row.get("requirement_text") or "").strip()
            clause = str(row.get("clause_ref") or row.get("clause") or "").strip()
            title = str(row.get("title") or row.get("name") or "").strip()
            if not (text or clause or title):
                continue
            records.append(
                (
                    artifact_id,
                    str(row.get("standard_name") or standard_name).strip(),
                    str(row.get("standard_version") or standard_version).strip(),
                    clause,
                    title,
                    text,
                    _normalize_obligation(row.get("obligation_level")),
                    _CURATED_CONFIDENCE,
                    text or title,
                    "OBSERVATION",
                    "NEW",
                    _CURATED_MODEL,
                    now,
                )
            )
        conn.executemany(
            """
            INSERT INTO candidate_requirements(
                artifact_id, standard_name, standard_version, clause_ref, title,
                requirement_text, obligation_level, confidence, supporting_text,
                knowledge_type, review_status, model, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )
        conn.commit()
        stats.requirements_imported = len(records)

    return stats


__all__ = ["ImportStats", "load_catalog", "import_standard"]
