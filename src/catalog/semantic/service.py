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

import dataclasses
import hashlib
import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..code import extract_structure, select_chunks, structure_to_result
from ..cost import NullUsageLedger, PricingTable, UsageLedger, load_pricing
from ..db import connect, init_db
from . import repository as repo
from .code_prompts import build_code_classification_prompt
from .domains import DomainTaxonomy, canonicalize_domains, load_domain_taxonomy
from .models import ClassificationResult
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


def _cache_artifact_dirs(cache_dir: Path, artifact_ids: list[str] | None) -> list[Path]:
    if artifact_ids is not None:
        return [
            cache_dir / aid
            for aid in artifact_ids
            if (cache_dir / aid / EXTRACTED_FILENAME).exists()
        ]
    return sorted(p.parent for p in cache_dir.glob(f"*/{EXTRACTED_FILENAME}") if p.is_file())


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
    usage_sink: list,
    prompt_builder=build_classification_prompt,
):
    """Classify every chunk with ``provider`` and merge the per-chunk results.

    ``prompt_builder`` selects the prompt schema (document vs. source code); both
    builders share a signature so this loop is identical for either.

    Per-chunk token usage is appended to ``usage_sink`` (the provider's
    ``last_usage``, or ``None`` for backends that do not report it) rather than
    written to the database, so this function is pure compute and safe to run off
    the main thread. The caller prices and persists the collected usages.
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
        usage_sink.append(getattr(provider, "last_usage", None))
        results.append(parse_classification_response(raw))
    return merge_classification_results(results)


@dataclass
class _WorkItem:
    """One document to classify, prepared on the main thread (no DB access left)."""

    artifact_dir: Path
    text: str
    source_hash: str
    metadata: dict


@dataclass
class _ComputeResult:
    """The LLM output for one document, ready to persist on the main thread."""

    artifact_dir: Path
    result: ClassificationResult
    model: str
    source_hash: str
    usages: list


def _prepare_work(
    conn, artifact_dirs: list[Path], *, force: bool
) -> tuple[list[_WorkItem], list[Path]]:
    """Read inputs and apply the incremental skip on the main thread.

    The skip check needs ``repo.get_source_hash`` (a DB read), so it runs here
    rather than in a worker. Returns the documents that need classifying plus the
    list of skipped (unchanged) artifact dirs.
    """

    items: list[_WorkItem] = []
    skipped: list[Path] = []
    for artifact_dir in artifact_dirs:
        text = (artifact_dir / EXTRACTED_FILENAME).read_text(encoding="utf-8")
        source_hash = _source_hash(text)
        if not force:
            existing = repo.get_source_hash(conn, artifact_dir.name)
            if existing is not None and existing == source_hash:
                LOGGER.debug("Skipping unchanged %s", artifact_dir.name)
                skipped.append(artifact_dir)
                continue
        items.append(
            _WorkItem(artifact_dir, text, source_hash, _read_metadata(artifact_dir))
        )
    return items, skipped


def _classify_compute(
    item: _WorkItem,
    router: ProviderRouter,
    *,
    max_input_chars: int,
    chunk_overlap: int,
    max_chunks: int,
    taxonomy: DomainTaxonomy,
) -> _ComputeResult:
    """Run all LLM work for one document. Pure compute - never touches the DB.

    This is the parallelizable half of classification: routing, chunked LLM
    calls, escalation, code-structure merge and domain canonicalization. Token
    usage is collected into the returned :class:`_ComputeResult` for the caller
    to persist on the main thread.
    """

    artifact_id = item.artifact_dir.name
    text = item.text
    metadata = item.metadata

    # Source files take the code-aware path: chunk along function/class
    # boundaries and classify with the code schema/prompt. Everything else keeps
    # the document path byte-for-byte.
    language = metadata.get("language")
    prompt_builder = build_code_classification_prompt if language else build_classification_prompt

    # Adaptive routing: a cheap, deterministic complexity read picks the model
    # (and chunk budget) for this document before any token is spent.
    decision: RouteDecision = router.route(text, metadata)
    chunk_cap = min(max_chunks, decision.max_chunks)

    # Process the whole document: split into chunks and merge per-chunk results
    # so equations and content past the head of a long document are not lost.
    chunks = select_chunks(text, language, max_input_chars, chunk_overlap)[:chunk_cap]
    provider = decision.provider
    usages: list = []
    result = _run_chunks(
        provider,
        metadata,
        chunks,
        max_input_chars=max_input_chars,
        usage_sink=usages,
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
        deep_chunks = select_chunks(text, language, max_input_chars, chunk_overlap)[:max_chunks]
        result = _run_chunks(
            provider,
            metadata,
            deep_chunks,
            max_input_chars=max_input_chars,
            usage_sink=usages,
            prompt_builder=prompt_builder,
        )

    # Fold in the deterministic code outline (modules/classes/functions/imports)
    # read straight from the syntax tree. Its type_confidence is 0.0 so the
    # model's summary/type still win the merge while the precise structural
    # entities and relationships are added (and beat lower-confidence duplicates).
    if language:
        structure = extract_structure(text, language)
        result = merge_classification_results([result, structure_to_result(structure, metadata)])

    # De-noise the discovered domains (confidence floor, canonical mapping, and
    # fuzzy-merge of near-duplicates) so one dense document does not surface a
    # dozen overlapping domains. Applied here, the single chokepoint covers both
    # the single-chunk and multi-chunk paths.
    result = dataclasses.replace(result, domains=canonicalize_domains(result.domains, taxonomy))

    return _ComputeResult(
        artifact_dir=item.artifact_dir,
        result=result,
        model=provider.model,
        source_hash=item.source_hash,
        usages=usages,
    )


def _persist_compute(conn, computed: _ComputeResult, *, now: str, stats: ClassifyStats, ledger) -> None:
    """Persist one document's classification and its priced usage (main thread)."""

    artifact_id = computed.artifact_dir.name
    result = computed.result

    repo.delete_for_artifact(conn, artifact_id)
    repo.persist_classification(
        conn,
        artifact_id=artifact_id,
        result=result,
        model=computed.model,
        source_hash=computed.source_hash,
        created_at=now,
    )
    for usage in computed.usages:
        if usage is not None:
            ledger.record_usage(usage, operation="classify", artifact_id=artifact_id)

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
    router_factory: Callable[[], ProviderRouter] | None = None,
    taxonomy: DomainTaxonomy | None = None,
    workers: int = 1,
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

    ``workers`` runs the per-document LLM calls concurrently (they are the
    dominant, network-bound cost). All database writes stay on this thread, so
    results are independent of worker count. Because a provider's ``last_usage``
    is mutable per-instance state, concurrent runs need a ``router_factory`` so
    each worker thread gets its own router/providers and token usage is
    attributed correctly; without one the shared ``router`` is reused (fine for
    backends that do not report usage, e.g. test stubs). ``workers=1`` runs the
    original serial path.
    """

    cache_path = Path(cache_dir)
    init_db(db_path)

    if router is None:
        router = router_factory() if router_factory is not None else single_provider_router(
            provider, max_chunks=max_chunks
        )
    if taxonomy is None:
        taxonomy = load_domain_taxonomy()

    # Each worker thread gets its own router (and thus its own provider
    # instances) when a factory is available, so the mutable ``last_usage`` state
    # is never shared across threads.
    thread_local = threading.local()

    def _worker_router() -> ProviderRouter:
        if router_factory is None:
            return router
        local = getattr(thread_local, "router", None)
        if local is None:
            local = router_factory()
            thread_local.router = local
        return local

    def _compute(item: _WorkItem):
        try:
            result = _classify_compute(
                item,
                _worker_router(),
                max_input_chars=max_input_chars,
                chunk_overlap=chunk_overlap,
                max_chunks=max_chunks,
                taxonomy=taxonomy,
            )
            return item, result, None
        except Exception as exc:  # noqa: BLE001 - surfaced and counted on the main thread
            return item, None, exc

    artifact_ids = _normalize_ids(artifact_id)
    started_at = _utc_now()
    stats = ClassifyStats()
    with connect(db_path) as conn:
        if track_cost:
            table = pricing if pricing is not None else load_pricing()
            ledger = UsageLedger(conn, table, provider_name=provider_name)
        else:
            ledger = NullUsageLedger()
        artifact_dirs = _artifact_dirs(cache_path, artifact_ids, _active_artifact_ids(conn))
        total_artifacts = len(artifact_dirs)
        work, skipped = _prepare_work(conn, artifact_dirs, force=force)
        stats.documents_skipped += len(skipped)

        completed = 0
        for artifact_dir in skipped:
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total_artifacts, artifact_dir.name)

        # Compute the LLM work (optionally in parallel), then persist each result
        # on this thread. pool.map preserves input order, so persistence is
        # deterministic regardless of worker count.
        if workers > 1 and len(work) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                computed = pool.map(_compute, work)
        else:
            computed = (_compute(item) for item in work)

        for item, result, exc in computed:
            completed += 1
            if exc is not None:
                if isinstance(exc, (LLMError, ParseError)):
                    LOGGER.warning(
                        "Classification failed for %s: %s", item.artifact_dir.name, exc
                    )
                else:
                    LOGGER.error(
                        "Unexpected classification error for %s",
                        item.artifact_dir, exc_info=exc,
                    )
                stats.errors += 1
            else:
                try:
                    _persist_compute(conn, result, now=started_at, stats=stats, ledger=ledger)
                    conn.commit()
                except Exception:  # noqa: BLE001 - one bad document must not abort the run
                    LOGGER.exception("Failed to persist classification for %s", item.artifact_dir)
                    stats.errors += 1
                    conn.rollback()
            if progress_callback is not None:
                progress_callback(completed, total_artifacts, item.artifact_dir.name)

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
