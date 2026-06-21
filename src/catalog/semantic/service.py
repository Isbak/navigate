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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..code import extract_structure, select_chunks, structure_to_result
from ..cost import NullUsageLedger, PricingTable, UsageLedger, load_pricing
from ..db import connect, init_db
from . import repository as repo
from .code_prompts import build_code_classification_prompt
from .parser import (
    ParseError,
    merge_classification_results,
    parse_classification_response,
)
from .prompts import build_classification_prompt
from .providers.base import BaseLLMProvider, LLMError
from .routing import ProviderRouter, RouteDecision, single_provider_router

LOGGER = logging.getLogger(__name__)

EXTRACTED_FILENAME = "extracted.txt"
METADATA_FILENAME = "metadata.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


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


def _normalize_ids(artifact_id: str | list[str] | None) -> list[str] | None:
    if artifact_id is None:
        return None
    if isinstance(artifact_id, str):
        return [artifact_id]
    return list(artifact_id)


def _cache_artifact_dirs(
    cache_dir: Path, artifact_ids: list[str] | None
) -> list[Path]:
    if artifact_ids is not None:
        return [
            cache_dir / aid
            for aid in artifact_ids
            if (cache_dir / aid / EXTRACTED_FILENAME).exists()
        ]
    return sorted(
        p.parent for p in cache_dir.glob(f"*/{EXTRACTED_FILENAME}") if p.is_file()
    )


def _active_artifact_ids(conn) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT id FROM artifacts WHERE scan_status != 'DELETED'"
    ).fetchall()
    return {row["id"] for row in rows}


def _artifact_dirs(
    cache_dir: Path, artifact_ids: list[str] | None, active_artifact_ids: set[str]
) -> list[Path]:
    cache_dirs = _cache_artifact_dirs(cache_dir, artifact_ids)
    if not active_artifact_ids:
        return cache_dirs
    return [p for p in cache_dirs if p.name in active_artifact_ids]


def _read_metadata(artifact_dir: Path) -> dict:
    path = artifact_dir / METADATA_FILENAME
    if not path.exists():
        return {"artifact_id": artifact_dir.name}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"artifact_id": artifact_dir.name}
    except json.JSONDecodeError:
        return {"artifact_id": artifact_dir.name}


def _run_chunks(
    provider: BaseLLMProvider,
    metadata: dict,
    chunks: list[str],
    *,
    max_input_chars: int,
    artifact_id: str,
    ledger,
    prompt_builder=build_classification_prompt,
):
    """Classify every chunk with ``provider`` and merge the per-chunk results.

    ``prompt_builder`` selects the prompt schema (document vs. source code); both
    builders share a signature so this loop is identical for either.
    """

    total = len(chunks)
    results = []
    for index, chunk in enumerate(chunks):
        system, user = prompt_builder(
            metadata,
            chunk,
            max_input_chars=max_input_chars,
            chunk_index=index,
            chunk_total=total,
        )
        raw = provider.generate(user, system=system)
        ledger.record(provider, operation="classify", artifact_id=artifact_id)
        results.append(parse_classification_response(raw))
    return merge_classification_results(results)


def _classify_one(
    conn,
    artifact_dir: Path,
    router: ProviderRouter,
    *,
    max_input_chars: int,
    chunk_overlap: int,
    max_chunks: int,
    force: bool,
    now: str,
    stats: ClassifyStats,
    ledger,
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

    # Source files take the code-aware path: chunk along function/class
    # boundaries and classify with the code schema/prompt. Everything else keeps
    # the document path byte-for-byte.
    language = metadata.get("language")
    prompt_builder = (
        build_code_classification_prompt if language else build_classification_prompt
    )

    # Adaptive routing: a cheap, deterministic complexity read picks the model
    # (and chunk budget) for this document before any token is spent.
    decision: RouteDecision = router.route(text, metadata)
    chunk_cap = min(max_chunks, decision.max_chunks)

    # Process the whole document: split into chunks and merge per-chunk results
    # so equations and content past the head of a long document are not lost.
    chunks = select_chunks(text, language, max_input_chars, chunk_overlap)[:chunk_cap]
    provider = decision.provider
    result = _run_chunks(
        provider,
        metadata,
        chunks,
        max_input_chars=max_input_chars,
        artifact_id=artifact_id,
        ledger=ledger,
        prompt_builder=prompt_builder,
    )

    # Escalation safety net: when the fast model is unsure about the document
    # type, re-run the document on the deep model and trust that result instead.
    if router.should_escalate(decision, result.type_confidence):
        LOGGER.debug(
            "Escalating %s to deep model (type_confidence=%.2f)",
            artifact_id,
            result.type_confidence,
        )
        provider = router.deep_provider
        deep_chunks = select_chunks(text, language, max_input_chars, chunk_overlap)[
            :max_chunks
        ]
        result = _run_chunks(
            provider,
            metadata,
            deep_chunks,
            max_input_chars=max_input_chars,
            artifact_id=artifact_id,
            ledger=ledger,
            prompt_builder=prompt_builder,
        )

    # Fold in the deterministic code outline (modules/classes/functions/imports)
    # read straight from the syntax tree. Its type_confidence is 0.0 so the
    # model's summary/type still win the merge while the precise structural
    # entities and relationships are added (and beat lower-confidence duplicates).
    if language:
        structure = extract_structure(text, language)
        result = merge_classification_results(
            [result, structure_to_result(structure, metadata)]
        )

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
    artifact_id: str | list[str] | None = None,
    force: bool = False,
    max_input_chars: int = 12000,
    chunk_overlap: int = 500,
    max_chunks: int = 20,
    progress_callback: Callable[[int, int, str], None] | None = None,
    pricing: PricingTable | None = None,
    provider_name: str | None = None,
    track_cost: bool = True,
    router: ProviderRouter | None = None,
) -> ClassifyStats:
    """Classify cached documents with ``provider`` and persist the results.

    Processes the given artifact id(s) when ``artifact_id`` is set (a single id
    or a list), otherwise every ``cache/*/extracted.txt``. Long documents are
    split into chunks of ``max_input_chars`` (with ``chunk_overlap`` overlap, up
    to ``max_chunks`` chunks) and the per-chunk results merged. Returns aggregate
    stats and records a row in ``classification_runs``. When provided,
    ``progress_callback`` is called after each artifact with
    ``(completed, total, artifact_id)``.

    ``router`` enables adaptive model routing: when given, it selects a fast or
    deep model per document (and may escalate uncertain results). When omitted,
    the single ``provider`` is used for every document - the original behaviour.
    """

    cache_path = Path(cache_dir)
    init_db(db_path)

    if router is None:
        router = single_provider_router(provider, max_chunks=max_chunks)

    artifact_ids = _normalize_ids(artifact_id)
    started_at = _utc_now()
    stats = ClassifyStats()
    with connect(db_path) as conn:
        if track_cost:
            table = pricing if pricing is not None else load_pricing()
            ledger = UsageLedger(conn, table, provider_name=provider_name)
        else:
            ledger = NullUsageLedger()
        artifact_dirs = _artifact_dirs(
            cache_path, artifact_ids, _active_artifact_ids(conn)
        )
        total_artifacts = len(artifact_dirs)
        for index, artifact_dir in enumerate(artifact_dirs, start=1):
            try:
                _classify_one(
                    conn,
                    artifact_dir,
                    router,
                    max_input_chars=max_input_chars,
                    chunk_overlap=chunk_overlap,
                    max_chunks=max_chunks,
                    force=force,
                    now=started_at,
                    stats=stats,
                    ledger=ledger,
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
            finally:
                if progress_callback is not None:
                    progress_callback(index, total_artifacts, artifact_dir.name)

        completed_at = _utc_now()
        repo.record_classification_run(
            conn,
            started_at=started_at,
            completed_at=completed_at,
            model=router.primary_model,
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
