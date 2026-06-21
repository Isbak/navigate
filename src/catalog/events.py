"""Scan event model and a lightweight publish/subscribe bus.

The scanner emits one :class:`ScanEvent` per artifact it processes. Future
extractors (text extraction, link discovery, LLM enrichment, RDF export, ...)
can subscribe to these events instead of being wired into the scan loop. This
keeps the scanner focused on reliable discovery and indexing while remaining
open for extension.

Data flow::

    scanner -> artifact queue -> database (+ event bus -> subscribers)
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum

LOGGER = logging.getLogger(__name__)


class ScanStatus(str, Enum):
    """Per-artifact status recorded by a scan."""

    RAW = "RAW"            # newly discovered file, never seen before
    CHANGED = "CHANGED"    # known path whose content hash changed
    UNCHANGED = "UNCHANGED"  # known path with identical content
    DELETED = "DELETED"    # previously indexed path no longer on disk
    DUPLICATE = "DUPLICATE"  # content hash already seen at another path

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


@dataclass(frozen=True)
class Artifact:
    """Metadata for a single discovered file."""

    id: str
    path: str
    filename: str
    file_type: str
    size_bytes: int
    created_at: str
    modified_at: str
    sha256: str
    source_system: str
    scan_status: ScanStatus
    last_scanned_at: str
    first_seen_at: str
    # Transient lifecycle flag, independent of ``scan_status`` so that a file
    # can be both NEW (lifecycle) and DUPLICATE (status) at the same time.
    lifecycle: ScanStatus = ScanStatus.RAW

    def to_row(self) -> dict:
        """Return a dict suitable for persisting in the ``artifacts`` table."""

        return {
            "id": self.id,
            "path": self.path,
            "filename": self.filename,
            "file_type": self.file_type,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "sha256": self.sha256,
            "source_system": self.source_system,
            "scan_status": self.scan_status.value,
            "last_scanned_at": self.last_scanned_at,
            "first_seen_at": self.first_seen_at,
        }


@dataclass
class ScanStats:
    """Aggregate counters for a single scan run."""

    files_scanned: int = 0
    new_files: int = 0
    changed_files: int = 0
    unchanged_files: int = 0
    duplicate_files: int = 0
    deleted_files: int = 0

    def record(self, artifact: Artifact) -> None:
        """Update counters from a processed artifact."""

        if artifact.scan_status is ScanStatus.DELETED:
            self.deleted_files += 1
            return
        self.files_scanned += 1
        if artifact.lifecycle is ScanStatus.RAW:
            self.new_files += 1
        elif artifact.lifecycle is ScanStatus.CHANGED:
            self.changed_files += 1
        elif artifact.lifecycle is ScanStatus.UNCHANGED:
            self.unchanged_files += 1
        if artifact.scan_status is ScanStatus.DUPLICATE:
            self.duplicate_files += 1

    def as_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "new_files": self.new_files,
            "changed_files": self.changed_files,
            "unchanged_files": self.unchanged_files,
            "duplicate_files": self.duplicate_files,
            "deleted_files": self.deleted_files,
        }


@dataclass(frozen=True)
class ScanEvent:
    """An event published for each artifact a scan processes."""

    status: ScanStatus
    artifact: Artifact


Subscriber = Callable[[ScanEvent], None]


@dataclass
class ScanEventBus:
    """Synchronous publish/subscribe dispatcher for scan events.

    Subscribers register a callback and, optionally, the set of statuses they
    care about. A failing subscriber is logged but never aborts the scan, so a
    misbehaving extractor cannot corrupt indexing.
    """

    _subscribers: list[tuple[frozenset[ScanStatus] | None, Subscriber]] = field(default_factory=list)

    def subscribe(self, callback: Subscriber, statuses: Iterable[ScanStatus] | None = None) -> None:
        """Register ``callback``; if ``statuses`` is given, only those fire it."""

        selector = frozenset(statuses) if statuses is not None else None
        self._subscribers.append((selector, callback))

    def publish(self, event: ScanEvent) -> None:
        """Deliver ``event`` to every interested subscriber."""

        for selector, callback in self._subscribers:
            if selector is not None and event.status not in selector:
                continue
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - isolate subscriber failures
                LOGGER.exception("Scan event subscriber failed for %s", event.artifact.path)
