"""Semantic classification service.

Reads the per-artifact cache produced by extraction
(``cache/<artifact_id>/metadata.json`` + ``extracted.txt``), asks an LLM to
*propose* structured knowledge, validates the response, and persists it as
observations and hypotheses - never facts.

    extraction  ->  cache/<id>/extracted.txt + metadata.json
    classify    ->  LLM proposes  ->  parse + validate  ->  SQLite (this module)

The LLM provider is injected (a :class:`BaseLLMProvider`), so the service has no
knowledge of Ollama vs OpenAI and is trivial to test with a stub provider.

Incremental processing: a document is classified only when its extraction
changed (the ``source_hash`` differs), its classification is missing, or
``force`` is set. Unchanged documents are skipped so re-runs are cheap.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..db import connect, init_db
from . import repository as repo
from .parser import ParseError, parse_classification_response
from .prompts import build_classification_prompt
from .providers.base import BaseLLMProvider, LLMError

LOGGER = logging.getLogger(__name__)

EXTRACTED_FILENAME = "extracted.txt"
METADATA_FILENAME = "metadata.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ClassifyStats:
    """Aggregate counters for one ``catalog classify`` run."""

    documents_processed: int = 0
    documents_skipped: int = 0
    errors: int = 0
    entities: int = 0
    capabilities: int = 0
    decisions: int = 0
    risks: int = 0
    relationships: int = 0
    by_document_type: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "documents_processed": self.documents_processed,
            "documents_skipped": self.documents_skipped,
            "errors": self.errors,
            "entities": self.entities,
            "capabilities": self.capabilities,
            "decisions": self.decisions,
            "risks": self.risks,
            "relationships": self.relationships,
        }


def _artifact_dirs(cache_dir: Path, artifact_id: str | None) -> list[Path]:
    if artifact_id is not None:
        candidate = cache_dir / artifact_id
        return [candidate] if (candidate / EXTRACTED_FILENAME).exists() else []
    return sorted(
        p.parent for p in cache_dir.glob(f"*/{EXTRACTED_FILENAME}") if p.is_file()
    )


def _read_metadata(artifact_dir: Path) -> dict:
    path = artifact_dir / METADATA_FILENAME
    if not path.exists():
        return {"artifact_id": artifact_dir.name}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"artifact_id": artifact_dir.name}
    except json.JSONDecodeError:
        return {"artifact_id": artifact_dir.name}


def _classify_one(
    conn,
    artifact_dir: Path,
    provider: BaseLLMProvider,
    *,
    max_input_chars: int,
    force: bool,
    now: str,
    stats: ClassifyStats,
) -> None:
    artifact_id = artifact_dir.name
    text = (artifact_dir / EXTRACTED_FILENAME).read_text(encoding="utf-8")
    source_hash = _source_hash(text)

    # Incremental skip: same extraction already classified, not forced.
    if not force:
        existing = repo.get_source_hash(conn, artifact_id)
        if existing is not None and existing == source_hash:
            stats.documents_skipped += 1
            LOGGER.debug("Skipping unchanged %s", artifact_id)
            return

    metadata = _read_metadata(artifact_dir)
    system, user = build_classification_prompt(
        metadata, text, max_input_chars=max_input_chars
    )
    raw = provider.generate(user, system=system)
    result = parse_classification_response(raw)

    repo.delete_for_artifact(conn, artifact_id)
    repo.persist_classification(
        conn,
        artifact_id=artifact_id,
        result=result,
        model=provider.model,
        source_hash=source_hash,
        created_at=now,
    )

    stats.documents_processed += 1
    stats.entities += len(result.entities)
    stats.capabilities += len(result.capabilities)
    stats.decisions += len(result.decisions)
    stats.risks += len(result.risks)
    stats.relationships += len(result.relationships)
    stats.by_document_type[result.document_type] = (
        stats.by_document_type.get(result.document_type, 0) + 1
    )


def classify_documents(
    db_path: str | Path,
    cache_dir: str | Path,
    provider: BaseLLMProvider,
    *,
    artifact_id: str | None = None,
    force: bool = False,
    max_input_chars: int = 12000,
) -> ClassifyStats:
    """Classify cached documents with ``provider`` and persist the results.

    Processes a single artifact when ``artifact_id`` is given, otherwise every
    ``cache/*/extracted.txt``. Returns aggregate stats and records a row in
    ``classification_runs``.
    """

    cache_path = Path(cache_dir)
    init_db(db_path)

    started_at = _utc_now()
    stats = ClassifyStats()
    artifact_dirs = _artifact_dirs(cache_path, artifact_id)

    with connect(db_path) as conn:
        for artifact_dir in artifact_dirs:
            try:
                _classify_one(
                    conn,
                    artifact_dir,
                    provider,
                    max_input_chars=max_input_chars,
                    force=force,
                    now=started_at,
                    stats=stats,
                )
                conn.commit()
            except (LLMError, ParseError) as exc:
                LOGGER.warning("Classification failed for %s: %s", artifact_dir.name, exc)
                stats.errors += 1
                conn.rollback()
            except Exception:  # noqa: BLE001 - one bad document must not abort the run
                LOGGER.exception("Unexpected classification error for %s", artifact_dir)
                stats.errors += 1
                conn.rollback()

        completed_at = _utc_now()
        repo.record_classification_run(
            conn,
            started_at=started_at,
            completed_at=completed_at,
            model=provider.model,
            documents_processed=stats.documents_processed,
            documents_skipped=stats.documents_skipped,
            errors=stats.errors,
        )
        conn.commit()

    LOGGER.info(
        "Classification complete: processed=%d skipped=%d errors=%d",
        stats.documents_processed,
        stats.documents_skipped,
        stats.errors,
    )
    return stats


__all__ = [
    "ClassifyStats",
    "classify_documents",
    "EXTRACTED_FILENAME",
    "METADATA_FILENAME",
]
