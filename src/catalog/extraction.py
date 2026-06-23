"""Text and raw-link extraction, implemented as a scan-event subscriber.

This module demonstrates the extension point described in the architecture: the
scanner only discovers and indexes metadata, while richer processing subscribes
to scan events. Future modules (LLM enrichment, RDF export, ...) can follow the
same pattern without touching the scanner.

Extraction is deliberately kept separate from link *discovery*: this module
only writes per-artifact cache files and never touches the database.

    cache/<artifact_id>/extracted.txt   full extracted text
    cache/<artifact_id>/links.json      raw links: [{raw_url, anchor_text}, ...]
    cache/<artifact_id>/metadata.json   artifact metadata for the discovery layer

The link discovery layer (``catalog.links``) reads ``links.json`` and persists
normalized, classified links to SQLite.

Only newly RAW or CHANGED artifacts need extraction. Because duplicates share a
content-addressed id (``doc_<sha>``), their extracted cache already exists, so
DUPLICATE/UNCHANGED events are intentionally ignored.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .code import detect_language, extract_structure
from .db import connect, init_db
from .events import Artifact, ScanEvent, ScanEventBus, ScanStatus
from .extractors import get_extractor
from .extractors.config import MODE_FAST

LOGGER = logging.getLogger(__name__)

URL_RE = re.compile(r"(?:https?|file|mailto):[^\s)\]>\"']+", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

EXTRACTED_FILENAME = "extracted.txt"
LINKS_FILENAME = "links.json"
METADATA_FILENAME = "metadata.json"
CODE_STRUCTURE_FILENAME = "code_structure.json"


def extract_text(
    path: Path, mode: str = MODE_FAST, usage_sink: list | None = None
) -> str:
    # Markdown, plain text, and source code are read verbatim; only binary
    # office/PDF formats need a dedicated extractor.
    if path.suffix.lower() in {".md", ".txt"} or detect_language(path):
        return path.read_text(encoding="utf-8", errors="replace")
    extractor = get_extractor(path, mode)
    if extractor is None:
        return ""
    text = extractor.extract_text(path)
    # The vision extractor records per-page token usage; hand it to the caller so
    # the database write stays in extract_all rather than the extractor.
    if usage_sink is not None and hasattr(extractor, "drain_usage"):
        usage_sink.extend(extractor.drain_usage())
    return text


def extract_links_from_text(text: str) -> list[dict[str, str | None]]:
    """Pull candidate links out of extracted text.

    Markdown links contribute anchor text; bare URLs are captured without one.
    Trailing whitespace only; punctuation cleanup and normalization happen later
    in the discovery layer so the raw URL is preserved here.
    """

    found: dict[str, str | None] = {}
    for anchor, url in MARKDOWN_LINK_RE.findall(text):
        if re.match(r"(?:https?|file|mailto):", url, re.IGNORECASE):
            found.setdefault(url, anchor)
    for url in URL_RE.findall(text):
        found.setdefault(url, None)
    return [{"raw_url": url, "anchor_text": anchor} for url, anchor in found.items()]


def extract_to_cache(
    artifact: Artifact,
    cache_dir: Path,
    mode: str = MODE_FAST,
    usage_sink: list | None = None,
) -> int:
    """Extract text + raw links for one artifact into its cache directory.

    Returns the number of raw links written. Never raises for extraction
    failures - text falls back to empty so the cache entry is still created.
    When ``usage_sink`` is given, any LLM token usage from vision extraction is
    appended to it (the caller prices and persists it).
    """

    path = Path(artifact.path)
    try:
        text = extract_text(path, mode, usage_sink=usage_sink)
    except Exception:  # noqa: BLE001 - extraction is best-effort
        LOGGER.exception("Text extraction failed for %s", path)
        text = ""

    artifact_cache = cache_dir / artifact.id
    artifact_cache.mkdir(parents=True, exist_ok=True)
    (artifact_cache / EXTRACTED_FILENAME).write_text(text, encoding="utf-8")

    raw_links = extract_links_from_text(text)
    (artifact_cache / LINKS_FILENAME).write_text(
        json.dumps(raw_links, indent=2), encoding="utf-8"
    )

    # Code-aware indexing: tag source files with their language and cache a
    # deterministic syntax outline alongside the text. Best-effort - a parse or
    # grammar problem leaves the language set but skips the structure sidecar.
    language = detect_language(path)
    if language:
        try:
            structure = extract_structure(text, language)
            (artifact_cache / CODE_STRUCTURE_FILENAME).write_text(
                json.dumps(structure.to_dict(), indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 - structure is an optional enrichment
            LOGGER.exception("Code structure extraction failed for %s", path)

    metadata = {
        "artifact_id": artifact.id,
        "path": artifact.path,
        "filename": artifact.filename,
        "file_type": artifact.file_type,
        "sha256": artifact.sha256,
        "source_system": artifact.source_system,
        "extracted_at": artifact.last_scanned_at,
        "link_count": len(raw_links),
        "language": language,
    }
    (artifact_cache / METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    LOGGER.debug("Extracted %d raw link(s) from %s", len(raw_links), path)
    return len(raw_links)


class ExtractionSubscriber:
    """Caches extracted text and raw hyperlinks when artifacts appear or change."""

    def __init__(
        self,
        db_path: str | Path = "data/catalog.sqlite",
        cache_dir: str | Path = "cache",
        mode: str = MODE_FAST,
    ) -> None:
        self.db_path = db_path
        self.cache_dir = Path(cache_dir)
        self.mode = mode

    def register(self, bus: ScanEventBus) -> None:
        bus.subscribe(self.handle, statuses={ScanStatus.RAW, ScanStatus.CHANGED})

    def handle(self, event: ScanEvent) -> None:
        artifact = event.artifact
        if not Path(artifact.path).exists():
            return
        extract_to_cache(artifact, self.cache_dir, self.mode)


def _artifact_from_row(row) -> Artifact:
    return Artifact(
        id=row["id"],
        path=row["path"],
        filename=row["filename"],
        file_type=row["file_type"],
        size_bytes=row["size_bytes"],
        created_at=row["created_at"],
        modified_at=row["modified_at"],
        sha256=row["sha256"],
        source_system=row["source_system"],
        scan_status=ScanStatus(row["scan_status"]),
        last_scanned_at=row["last_scanned_at"],
        first_seen_at=row["first_seen_at"],
    )


def _matches_glob(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern)


def _extract_one(row, cache_path: Path, mode: str) -> tuple[int, list[tuple[str, object]]]:
    """Extract a single artifact into its cache dir (no DB access).

    Returns ``(links_extracted, [(artifact_id, Usage), ...])``. Safe to run
    concurrently: each call writes only to its own ``cache/<id>/`` directory and
    accumulates vision-extraction usage in a private list.
    """

    usages: list = []
    links = extract_to_cache(
        _artifact_from_row(row), cache_path, mode, usage_sink=usages
    )
    return links, [(row["id"], usage) for usage in usages]


def extract_all(
    db_path: str | Path = "data/catalog.sqlite",
    cache_dir: str | Path = "cache",
    mode: str = MODE_FAST,
    artifact_ids: list[str] | None = None,
    path_glob: str | None = None,
    workers: int = 1,
) -> dict:
    """(Re)build the extraction cache for indexed, on-disk artifacts.

    Duplicates share a content id and therefore a single cache entry, so each
    distinct id is extracted once. ``artifact_ids`` and/or ``path_glob`` narrow
    the run to a chosen subset (e.g. re-extract just the equation-heavy PDFs in
    ``high-quality`` mode); when both are ``None`` every artifact is processed.

    ``workers`` sets the size of the extraction thread pool. Each artifact is
    independent (it writes only to its own cache dir and the loop never touches
    the database), so the work is embarrassingly parallel; ``workers=1`` runs the
    original serial path. Returns summary counters.
    """

    cache_path = Path(cache_dir)
    id_filter = set(artifact_ids) if artifact_ids else None
    artifacts_processed = 0
    links_extracted = 0
    errors = 0
    seen_ids: set[str] = set()
    # (artifact_id, Usage) pairs from vision extraction, priced and persisted in
    # one transaction after the batch so the cache pass stays free of DB writes.
    collected_usage: list[tuple[str, object]] = []

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE scan_status != 'DELETED'"
        ).fetchall()

    # Pre-filter on the main thread so the worker function stays pure.
    pending = []
    for row in rows:
        if row["id"] in seen_ids:
            continue
        if id_filter is not None and row["id"] not in id_filter:
            continue
        if path_glob is not None and not _matches_glob(row["path"], path_glob):
            continue
        if not Path(row["path"]).exists():
            continue
        seen_ids.add(row["id"])
        pending.append(row)

    def _run(row):
        try:
            links, usages = _extract_one(row, cache_path, mode)
            return links, usages, None
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the batch
            return 0, [], (row["path"], exc)

    if workers > 1 and len(pending) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = pool.map(_run, pending)
    else:
        results = (_run(row) for row in pending)

    for links, usages, error in results:
        if error is not None:
            path, exc = error
            LOGGER.error("Extraction failed for %s: %s", path, exc, exc_info=exc)
            errors += 1
            continue
        links_extracted += links
        collected_usage.extend(usages)
        artifacts_processed += 1

    _record_extraction_usage(db_path, collected_usage)

    return {
        "artifacts_processed": artifacts_processed,
        "links_extracted": links_extracted,
        "errors": errors,
    }


def _record_extraction_usage(
    db_path: str | Path, collected_usage: list[tuple[str, object]]
) -> None:
    """Price and persist vision-extraction token usage. No-op when empty."""

    if not collected_usage:
        return
    from .cost import UsageLedger, load_pricing
    from .semantic.config import load_llm_config

    init_db(db_path)
    pricing = load_pricing()
    try:
        provider_name = load_llm_config().provider
    except Exception:  # noqa: BLE001 - a config problem must not fail extraction
        provider_name = None
    with connect(db_path) as conn:
        ledger = UsageLedger(conn, pricing, provider_name=provider_name)
        for artifact_id, usage in collected_usage:
            ledger.record_usage(
                usage, operation="vision-extract", artifact_id=artifact_id
            )
        conn.commit()
