"""ConnectorSync: orchestrates the download → artifact → event-bus pipeline.

For each enabled connector the sync loop:
  1. Calls ``connector.list_documents()`` to get the current remote manifest.
  2. Compares against ``connector_file_map`` to detect new / changed / deleted items.
  3. Downloads only items that changed (or are new).
  4. Upserts artifacts into SQLite and fires ``ScanEvent`` so extraction and
     classification subscribers run without any extra wiring.
  5. Tombstones items that disappeared from the remote.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..db import connect, init_db, mark_deleted, upsert_artifact
from ..events import Artifact, ScanEvent, ScanEventBus, ScanStatus
from .base import BaseConnector, ConnectorError, ConnectorStats, RemoteDocument

LOGGER = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _document_id(sha256: str) -> str:
    return f"doc_{sha256[:12]}"


def _load_file_map(conn: sqlite3.Connection, connector_name: str) -> dict[str, tuple[str, str]]:
    """Return ``{remote_id: (local_path, remote_modified_at)}`` for one connector."""
    rows = conn.execute(
        "SELECT remote_id, local_path, remote_modified_at "
        "FROM connector_file_map WHERE connector_name=?",
        (connector_name,),
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def _upsert_file_map(
    conn: sqlite3.Connection,
    connector_name: str,
    remote_id: str,
    local_path: str,
    remote_modified_at: str,
    synced_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO connector_file_map(connector_name, remote_id, local_path, remote_modified_at, synced_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(connector_name, remote_id) DO UPDATE SET
          local_path=excluded.local_path,
          remote_modified_at=excluded.remote_modified_at,
          synced_at=excluded.synced_at
        """,
        (connector_name, remote_id, local_path, remote_modified_at, synced_at),
    )


def _delete_file_map(conn: sqlite3.Connection, connector_name: str, remote_id: str) -> None:
    conn.execute(
        "DELETE FROM connector_file_map WHERE connector_name=? AND remote_id=?",
        (connector_name, remote_id),
    )


def _local_path_for(cache_dir: Path, connector_name: str, doc: RemoteDocument) -> Path:
    """Derive a stable local path for a remote document.

    A short SHA-1 of ``remote_id`` is used as the subdirectory so long IDs or
    special characters never cause filesystem issues, while the original filename
    is preserved for human-readable access and correct extension detection.
    """
    id_hash = hashlib.sha1(doc.remote_id.encode()).hexdigest()[:16]
    return cache_dir / connector_name / id_hash / doc.name


def _build_artifact(
    local_path: Path,
    doc: RemoteDocument,
    sha256: str,
    source_system: str,
    synced_at: str,
    first_seen_at: str,
    lifecycle: ScanStatus,
) -> Artifact:
    return Artifact(
        id=_document_id(sha256),
        path=str(local_path.resolve()),
        filename=doc.name,
        file_type=doc.file_type,
        size_bytes=local_path.stat().st_size,
        created_at=doc.created_at,
        modified_at=doc.modified_at,
        sha256=sha256,
        source_system=source_system,
        scan_status=lifecycle,
        last_scanned_at=synced_at,
        first_seen_at=first_seen_at,
        lifecycle=lifecycle,
    )


class ConnectorSync:
    """Download remote content and integrate it into the artifact pipeline."""

    def __init__(
        self,
        db_path: str | Path,
        cache_dir: str | Path,
        event_bus: ScanEventBus | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.cache_dir = Path(cache_dir)
        self.event_bus = event_bus or ScanEventBus()

    def sync(self, connector: BaseConnector, *, dry_run: bool = False) -> ConnectorStats:
        """Sync one connector: list → diff → download → upsert → fire events."""

        stats = ConnectorStats(connector_name=connector.name)
        synced_at = _utc_now()

        init_db(self.db_path)

        seen_remote_ids: set[str] = set()

        with connect(self.db_path) as conn:
            existing_map = _load_file_map(conn, connector.name)

            try:
                for doc in connector.list_documents():
                    seen_remote_ids.add(doc.remote_id)
                    prior = existing_map.get(doc.remote_id)

                    if prior is not None and prior[1] == doc.modified_at:
                        stats.unchanged_files += 1
                        continue

                    lifecycle = ScanStatus.CHANGED if prior is not None else ScanStatus.RAW
                    local_path = _local_path_for(self.cache_dir, connector.name, doc)

                    if dry_run:
                        verb = "would add" if lifecycle is ScanStatus.RAW else "would update"
                        print(f"[dry-run] {verb}: {doc.remote_id}")
                        if lifecycle is ScanStatus.RAW:
                            stats.new_files += 1
                        else:
                            stats.changed_files += 1
                        continue

                    try:
                        content = connector.fetch_content(doc)
                    except ConnectorError as exc:
                        LOGGER.error(
                            "Failed to fetch %s from %s: %s", doc.remote_id, connector.name, exc
                        )
                        stats.errors += 1
                        continue

                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(content)

                    sha256 = _sha256_bytes(content)
                    local_path_str = str(local_path.resolve())

                    row = conn.execute(
                        "SELECT first_seen_at FROM artifacts WHERE path=?",
                        (local_path_str,),
                    ).fetchone()
                    first_seen_at = row["first_seen_at"] if row else synced_at

                    artifact = _build_artifact(
                        local_path, doc, sha256, connector.name,
                        synced_at, first_seen_at, lifecycle,
                    )
                    upsert_artifact(conn, artifact.to_row())
                    _upsert_file_map(
                        conn, connector.name, doc.remote_id,
                        local_path_str, doc.modified_at, synced_at,
                    )
                    # Commit before publishing so subscribers opening their own
                    # connections see the committed row (mirrors scanner behaviour).
                    conn.commit()

                    self.event_bus.publish(ScanEvent(status=lifecycle, artifact=artifact))

                    if lifecycle is ScanStatus.RAW:
                        stats.new_files += 1
                    else:
                        stats.changed_files += 1

                    LOGGER.debug(
                        "%s %s (%s)", lifecycle.value, doc.remote_id, artifact.id
                    )

            except ConnectorError as exc:
                LOGGER.error(
                    "Connector %s failed during listing: %s", connector.name, exc
                )
                stats.errors += 1
                return stats

            # Tombstone items that have disappeared from the remote.
            if not dry_run:
                for remote_id, (local_path_str, _) in existing_map.items():
                    if remote_id not in seen_remote_ids:
                        mark_deleted(conn, local_path_str, synced_at)
                        _delete_file_map(conn, connector.name, remote_id)
                        conn.commit()
                        stats.deleted_files += 1
                        LOGGER.debug("DELETED %s (no longer in remote)", remote_id)

        return stats
