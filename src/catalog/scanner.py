"""Artifact scanner: discover supported documents and index their metadata.

The scanner walks the configured source folders, computes stable, content
addressed identities, detects what changed since the previous scan, and feeds
records through an artifact queue into SQLite. Every processed artifact is
published on a :class:`~catalog.events.ScanEventBus` so future extractors can
subscribe without modifying the scanner.

    scanner -> artifact queue -> database
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .code import CODE_EXTENSIONS
from .config import load_config
from .db import (
    connect,
    existing_artifacts,
    init_db,
    mark_deleted,
    record_scan_run,
    upsert_artifact,
)
from .events import Artifact, ScanEvent, ScanEventBus, ScanStats, ScanStatus
from .hashing import document_id, sha256_file
from .queue import ArtifactQueue

LOGGER = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".pdf", ".md", ".txt"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def is_excluded(path: Path, patterns: list[str]) -> bool:
    value = path.as_posix()
    return any(
        fnmatch.fnmatch(value, pattern) or fnmatch.fnmatch(path.name, pattern)
        for pattern in patterns
    )


def iter_documents(
    source: Path,
    exclude: list[str],
    extensions: set[str] | frozenset[str] = SUPPORTED_EXTENSIONS,
) -> Iterator[Path]:
    """Yield supported, non-excluded files beneath ``source`` recursively.

    ``extensions`` is the effective set to accept; the scanner passes
    ``SUPPORTED_EXTENSIONS`` plus the code extensions when code indexing is on.
    """

    if not source.exists():
        LOGGER.warning("Source path does not exist: %s", source)
        return
    for path in source.rglob("*"):
        if (
            path.is_file()
            and path.suffix.lower() in extensions
            and not is_excluded(path, exclude)
        ):
            yield path


def _build_artifact(
    path: Path,
    source_system: str,
    existing: dict[str, object],
    scanned_at: str,
) -> Artifact:
    """Hash ``path``, read its metadata, and classify it against the index."""

    resolved = str(path)
    digest = sha256_file(path)
    stat = path.stat()
    prior = existing.get(resolved)

    if prior is None:
        lifecycle = ScanStatus.RAW
        first_seen_at = scanned_at
    elif prior["sha256"] == digest:
        lifecycle = ScanStatus.UNCHANGED
        first_seen_at = prior["first_seen_at"] or scanned_at
    else:
        lifecycle = ScanStatus.CHANGED
        first_seen_at = prior["first_seen_at"] or scanned_at

    return Artifact(
        id=document_id(digest),
        path=resolved,
        filename=path.name,
        file_type=path.suffix.lower().lstrip("."),
        size_bytes=stat.st_size,
        created_at=_to_iso(stat.st_ctime),
        modified_at=_to_iso(stat.st_mtime),
        sha256=digest,
        source_system=source_system,
        scan_status=lifecycle,
        last_scanned_at=scanned_at,
        first_seen_at=first_seen_at,
        lifecycle=lifecycle,
    )


def _flag_duplicates(artifacts: list[Artifact], existing: dict[str, object]) -> list[Artifact]:
    """Mark every redundant copy of identical content as DUPLICATE.

    For each group of files sharing a sha256 the "primary" keeps its lifecycle
    status; the remaining copies become DUPLICATE. An already-indexed path is
    preferred as the primary so a freshly-added copy is the one flagged.
    """

    by_sha: dict[str, list[Artifact]] = {}
    for artifact in artifacts:
        by_sha.setdefault(artifact.sha256, []).append(artifact)

    result: list[Artifact] = []
    for group in by_sha.values():
        if len(group) == 1:
            result.append(group[0])
            continue
        primary = min(
            group,
            key=lambda a: (a.path not in existing, a.path),
        )
        for artifact in group:
            if artifact is primary:
                result.append(artifact)
            else:
                result.append(
                    Artifact(**{**artifact.__dict__, "scan_status": ScanStatus.DUPLICATE})
                )
    return result


class Scanner:
    """Orchestrates a scan: discovery -> artifact queue -> database + events."""

    def __init__(
        self,
        db_path: str | Path = "data/catalog.sqlite",
        event_bus: ScanEventBus | None = None,
    ) -> None:
        self.db_path = db_path
        self.event_bus = event_bus or ScanEventBus()

    # -- consumer ---------------------------------------------------------
    def _consume(self, artifact_queue: ArtifactQueue, stats: ScanStats) -> None:
        """Drain the queue on this thread, persisting and announcing each item."""

        with connect(self.db_path) as conn:
            for artifact in artifact_queue.drain():
                if artifact.scan_status is ScanStatus.DELETED:
                    mark_deleted(conn, artifact.path, artifact.last_scanned_at)
                else:
                    upsert_artifact(conn, artifact.to_row())
                # Commit before notifying so subscribers (which open their own
                # connections) don't deadlock against an open write transaction.
                conn.commit()
                stats.record(artifact)
                self.event_bus.publish(ScanEvent(status=artifact.scan_status, artifact=artifact))
                LOGGER.debug("%s %s (%s)", artifact.scan_status, artifact.path, artifact.id)

    # -- public API -------------------------------------------------------
    def scan(self, config_path: str | Path = "config/sources.yml") -> ScanStats:
        cfg = load_config(config_path)
        init_db(self.db_path)
        started_at = utc_now()
        scanned_at = started_at

        # Code-aware indexing extends the accepted file types with source code.
        extensions = SUPPORTED_EXTENSIONS | (
            CODE_EXTENSIONS if cfg.index_code else frozenset()
        )

        with connect(self.db_path) as conn:
            existing = existing_artifacts(conn)

        # Producer: discover + classify on this thread.
        discovered: list[Artifact] = []
        seen_paths: set[str] = set()
        for source in cfg.sources:
            root = Path(source.path).expanduser()
            for path in iter_documents(root, cfg.exclude, extensions):
                resolved = str(path.resolve())
                try:
                    artifact = _build_artifact(
                        path.resolve(), source.source_system, existing, scanned_at
                    )
                except OSError:
                    LOGGER.exception("Failed to read %s", path)
                    continue
                discovered.append(artifact)
                seen_paths.add(resolved)

        discovered = _flag_duplicates(discovered, existing)

        stats = ScanStats()
        artifact_queue = ArtifactQueue()
        consumer = threading.Thread(
            target=self._consume, args=(artifact_queue, stats), name="artifact-writer"
        )
        consumer.start()

        for artifact in discovered:
            artifact_queue.put(artifact)

        # Tombstones for paths that disappeared since the last scan.
        for path, row in existing.items():
            if path not in seen_paths and row["scan_status"] != ScanStatus.DELETED.value:
                artifact_queue.put(
                    Artifact(
                        id=row["id"],
                        path=path,
                        filename=row["filename"],
                        file_type=row["file_type"],
                        size_bytes=row["size_bytes"],
                        created_at=row["created_at"],
                        modified_at=row["modified_at"],
                        sha256=row["sha256"],
                        source_system=row["source_system"],
                        scan_status=ScanStatus.DELETED,
                        last_scanned_at=scanned_at,
                        first_seen_at=row["first_seen_at"] or scanned_at,
                        lifecycle=ScanStatus.DELETED,
                    )
                )

        artifact_queue.close()
        consumer.join()

        finished_at = utc_now()
        with connect(self.db_path) as conn:
            record_scan_run(conn, started_at, finished_at, stats.as_dict())
        LOGGER.info(
            "Scan complete: scanned=%d new=%d changed=%d duplicates=%d deleted=%d",
            stats.files_scanned,
            stats.new_files,
            stats.changed_files,
            stats.duplicate_files,
            stats.deleted_files,
        )
        return stats

    def scan_path(self, path: str | Path, source_system: str = "local_laptop") -> Artifact:
        """Incrementally (re)index a single file, for use by the watcher."""

        init_db(self.db_path)
        resolved = Path(path).expanduser().resolve()
        scanned_at = utc_now()
        with connect(self.db_path) as conn:
            existing = existing_artifacts(conn)
        artifact = _build_artifact(resolved, source_system, existing, scanned_at)
        # A single-file rescan can only see duplicates already in the index.
        for other_path, row in existing.items():
            if other_path != str(resolved) and row["sha256"] == artifact.sha256:
                artifact = Artifact(**{**artifact.__dict__, "scan_status": ScanStatus.DUPLICATE})
                break
        with connect(self.db_path) as conn:
            upsert_artifact(conn, artifact.to_row())
        self.event_bus.publish(ScanEvent(status=artifact.scan_status, artifact=artifact))
        LOGGER.info("Indexed %s as %s (%s)", resolved, artifact.id, artifact.scan_status)
        return artifact


def build_default_scanner(
    db_path: str | Path = "data/catalog.sqlite", cache_dir: str | Path = "cache"
) -> Scanner:
    """Create a Scanner with the bundled text/link extraction subscriber wired in."""

    from .extraction import ExtractionSubscriber

    bus = ScanEventBus()
    ExtractionSubscriber(db_path=db_path, cache_dir=cache_dir).register(bus)
    return Scanner(db_path=db_path, event_bus=bus)


def scan(
    config_path: str | Path = "config/sources.yml",
    db_path: str | Path = "data/catalog.sqlite",
    cache_dir: str | Path = "cache",
) -> ScanStats:
    """Convenience entry point used by the CLI."""

    return build_default_scanner(db_path, cache_dir).scan(config_path)


def scan_file(
    path: str | Path,
    source_system: str = "local_laptop",
    db_path: str | Path = "data/catalog.sqlite",
    cache_dir: str | Path = "cache",
) -> str:
    """Index a single file and return its artifact id (used by the watcher)."""

    artifact = build_default_scanner(db_path, cache_dir).scan_path(path, source_system)
    return artifact.id
