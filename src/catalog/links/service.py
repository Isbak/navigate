"""Link discovery service.

Reads the per-artifact ``links.json`` files produced by the extraction layer,
normalizes and classifies each URL, and persists the results in SQLite. This is
the discovery half of the deliberately separated pipeline:

    extraction  ->  cache/<artifact_id>/links.json   (raw links, no DB)
    discovery   ->  reads links.json, writes SQLite   (this module)

No source documents are read or modified here, and there is no LLM, semantic
analysis, or RDF involved - only deterministic normalization and persistence.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..db import connect, init_db
from . import repository as repo
from .classifier import classify
from .config import LinkConfig, load_link_config
from .normalizer import normalize_url

LOGGER = logging.getLogger(__name__)

LINKS_FILENAME = "links.json"
METADATA_FILENAME = "metadata.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class LinkScanStats:
    """Aggregate counters for one ``discover-links`` run."""

    artifacts_processed: int = 0
    links_found: int = 0
    links_new: int = 0
    links_updated: int = 0
    links_removed: int = 0
    errors: int = 0
    by_system: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "artifacts_processed": self.artifacts_processed,
            "links_found": self.links_found,
            "links_new": self.links_new,
            "links_updated": self.links_updated,
            "links_removed": self.links_removed,
            "errors": self.errors,
        }


def _normalize_anchor(anchor: object) -> str | None:
    if anchor is None:
        return None
    text = str(anchor).strip()
    return text or None


def _read_raw_links(links_path: Path) -> list[dict]:
    """Parse a ``links.json`` file into ``[{raw_url, anchor_text}, ...]``.

    Tolerates either a list of objects (``{"raw_url"/"url", "anchor_text"}``)
    or a bare list of URL strings.
    """

    data = json.loads(links_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    raw_links: list[dict] = []
    for item in data:
        if isinstance(item, str):
            raw_links.append({"raw_url": item, "anchor_text": None})
        elif isinstance(item, dict):
            url = item.get("raw_url") or item.get("url") or item.get("target_url")
            if not url:
                continue
            anchor = item.get("anchor_text", item.get("anchor"))
            raw_links.append({"raw_url": str(url), "anchor_text": anchor})
    return raw_links


def _artifact_dirs(cache_dir: Path, artifact_id: str | None) -> list[Path]:
    if artifact_id is not None:
        candidate = cache_dir / artifact_id
        return [candidate] if (candidate / LINKS_FILENAME).exists() else []
    return sorted(
        p.parent for p in cache_dir.glob(f"*/{LINKS_FILENAME}") if p.is_file()
    )


@dataclass(frozen=True)
class _Resolved:
    normalized_url: str
    target_system: str
    target_type: str
    link_kind: str


def _classify_and_normalize(raw_url: str, config: LinkConfig) -> _Resolved:
    normalized_url = normalize_url(raw_url)
    if not normalized_url:
        return _Resolved("", "unknown", "unknown", "unknown")
    classification = classify(normalized_url, config)
    return _Resolved(
        normalized_url=normalized_url,
        target_system=classification.target_system,
        target_type=classification.target_type,
        link_kind=classification.link_kind,
    )


@dataclass(frozen=True)
class _ResolvedLink:
    raw_url: str
    normalized_url: str
    anchor_text: str | None
    target_system: str
    target_type: str
    link_kind: str


def _resolve_artifact_links(
    artifact_dir: Path, config: LinkConfig
) -> tuple[str, list[_ResolvedLink]]:
    """Read and classify one artifact's links - pure compute, no DB access.

    Reading ``links.json`` plus URL normalization/classification is the only
    CPU/IO work in link discovery, and it touches no shared state, so it is safe
    to run concurrently. The returned records are persisted on a single thread.
    """

    artifact_id = artifact_dir.name
    raw_links = _read_raw_links(artifact_dir / LINKS_FILENAME)
    resolved: list[_ResolvedLink] = []
    for raw in raw_links:
        raw_url = raw["raw_url"]
        r = _classify_and_normalize(raw_url, config)
        if not r.normalized_url:
            continue
        resolved.append(
            _ResolvedLink(
                raw_url=raw_url,
                normalized_url=r.normalized_url,
                anchor_text=_normalize_anchor(raw.get("anchor_text")),
                target_system=r.target_system,
                target_type=r.target_type,
                link_kind=r.link_kind,
            )
        )
    return artifact_id, resolved


def _persist_artifact_links(
    conn,
    artifact_id: str,
    resolved: list[_ResolvedLink],
    seen_at: str,
    stats: LinkScanStats,
) -> None:
    for link in resolved:
        result = repo.upsert_link(
            conn,
            source_artifact_id=artifact_id,
            raw_url=link.raw_url,
            normalized_url=link.normalized_url,
            anchor_text=link.anchor_text,
            target_system=link.target_system,
            target_type=link.target_type,
            link_kind=link.link_kind,
            seen_at=seen_at,
        )
        stats.links_found += 1
        if result.is_new:
            stats.links_new += 1
        else:
            stats.links_updated += 1

    stats.links_removed += repo.mark_stale_for_artifact(conn, artifact_id, seen_at)
    stats.artifacts_processed += 1


def discover_links(
    db_path: str | Path = "data/catalog.sqlite",
    cache_dir: str | Path = "cache",
    config: LinkConfig | None = None,
    artifact_id: str | None = None,
    workers: int = 1,
) -> LinkScanStats:
    """Scan cached ``links.json`` files and persist normalized links.

    When ``artifact_id`` is given only that artifact's cache is processed;
    otherwise every ``cache/*/links.json`` is read. Returns aggregate stats and
    records a row in ``link_scan_runs``.

    ``workers`` parallelizes the read/normalize/classify step across a thread
    pool; the SQLite writes stay on this (single) thread to honor SQLite's
    one-writer rule, so results are independent of worker count. ``workers=1``
    runs the original serial path.
    """

    config = config or LinkConfig.empty()
    cache_path = Path(cache_dir)
    init_db(db_path)

    started_at = _utc_now()
    stats = LinkScanStats()

    artifact_dirs = _artifact_dirs(cache_path, artifact_id)

    def _resolve(artifact_dir: Path):
        try:
            aid, resolved = _resolve_artifact_links(artifact_dir, config)
            return artifact_dir, aid, resolved, None
        except Exception as exc:  # noqa: BLE001 - one bad artifact must not abort the run
            return artifact_dir, artifact_dir.name, [], exc

    # Compute first (parallel), then persist on one thread. pool.map preserves
    # input order, so persistence order is deterministic regardless of workers.
    if workers > 1 and len(artifact_dirs) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            resolved_items = list(pool.map(_resolve, artifact_dirs))
    else:
        resolved_items = [_resolve(d) for d in artifact_dirs]

    with connect(db_path) as conn:
        for artifact_dir, aid, resolved, error in resolved_items:
            if error is not None:
                LOGGER.error(
                    "Link discovery failed for %s: %s", artifact_dir, error,
                    exc_info=error,
                )
                stats.errors += 1
                continue
            try:
                _persist_artifact_links(conn, aid, resolved, started_at, stats)
                conn.commit()
            except Exception:  # noqa: BLE001 - one bad artifact must not abort the run
                LOGGER.exception("Link discovery failed for %s", artifact_dir)
                stats.errors += 1
                conn.rollback()

        completed_at = _utc_now()
        stats.by_system = {
            row["key"]: row["count"] for row in repo.counts_by(conn, "target_system")
        }
        repo.record_link_scan_run(
            conn,
            started_at=started_at,
            completed_at=completed_at,
            **stats.as_dict(),
        )
        conn.commit()

    LOGGER.info(
        "Link discovery complete: artifacts=%d found=%d new=%d updated=%d stale=%d errors=%d",
        stats.artifacts_processed,
        stats.links_found,
        stats.links_new,
        stats.links_updated,
        stats.links_removed,
        stats.errors,
    )
    return stats


__all__ = [
    "LinkScanStats",
    "discover_links",
    "load_link_config",
    "LINKS_FILENAME",
    "METADATA_FILENAME",
]
