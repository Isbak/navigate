"""Text and link extraction, implemented as a scan-event subscriber.

This module demonstrates the extension point described in the architecture: the
scanner only discovers and indexes metadata, while richer processing subscribes
to scan events. Future modules (LLM enrichment, RDF export, ...) can follow the
same pattern without touching the scanner.

Only newly RAW or CHANGED artifacts need extraction. Because duplicates share a
content-addressed id (``doc_<sha>``), their extracted cache already exists, so
DUPLICATE/UNCHANGED events are intentionally ignored.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .db import connect, replace_links
from .events import ScanEvent, ScanEventBus, ScanStatus
from .extractors import get_extractor
from .links import classify_target_system, classify_target_type, extract_links_from_text

LOGGER = logging.getLogger(__name__)


def extract_text(path: Path) -> str:
    if path.suffix.lower() in {".md", ".txt"}:
        return path.read_text(encoding="utf-8", errors="replace")
    extractor = get_extractor(path)
    if extractor is None:
        return ""
    return extractor.extract_text(path)


class ExtractionSubscriber:
    """Extracts cached text and hyperlinks when artifacts appear or change."""

    def __init__(
        self,
        db_path: str | Path = "data/catalog.sqlite",
        cache_dir: str | Path = "cache",
    ) -> None:
        self.db_path = db_path
        self.cache_dir = Path(cache_dir)

    def register(self, bus: ScanEventBus) -> None:
        bus.subscribe(self.handle, statuses={ScanStatus.RAW, ScanStatus.CHANGED})

    def handle(self, event: ScanEvent) -> None:
        artifact = event.artifact
        path = Path(artifact.path)
        if not path.exists():
            return

        try:
            text = extract_text(path)
        except Exception:  # noqa: BLE001 - extraction is best-effort
            LOGGER.exception("Text extraction failed for %s", path)
            text = ""

        artifact_cache = self.cache_dir / artifact.id
        artifact_cache.mkdir(parents=True, exist_ok=True)
        (artifact_cache / "extracted.txt").write_text(text, encoding="utf-8")

        links = []
        for link in extract_links_from_text(text):
            url = str(link["target_url"])
            links.append(
                {
                    "source_path": artifact.path,
                    "target_url": url,
                    "anchor_text": link.get("anchor_text"),
                    "target_system": classify_target_system(url),
                    "target_type": classify_target_type(url),
                    "discovered_at": artifact.last_scanned_at,
                }
            )

        with connect(self.db_path) as conn:
            replace_links(conn, artifact.path, links)
        LOGGER.debug("Extracted %d link(s) from %s", len(links), path)
