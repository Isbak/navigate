"""The ``clean-source`` purge: remove all material tied to a file or folder.

Scanning soft-deletes vanished files and consolidation simply ignores
out-of-scope documents, so material from a removed folder lingers in the
``candidate_*`` tables. ``purge_path`` is the explicit, permanent counterpart: it
deletes the artifact rows under a path together with their semantic candidates,
classification, links, and extraction cache, then re-consolidates so any derived
knowledge object or relationship that came solely from those files disappears.

The unit of identity is the content-addressed ``artifact_id``, which byte-identical
duplicates share. So an id is only fully purged when **every** path carrying it
lives under the target ("exclusive"); if a duplicate survives elsewhere ("shared")
the artifact rows under the target are still removed, but the shared candidates,
links, and cache are kept and a warning is emitted so the surviving copy is not
orphaned.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_config
from ..db import artifacts_under_path, connect, delete_artifacts_by_paths, init_db
from ..knowledge.service import consolidate
from ..links import repository as link_repo
from ..semantic import repository as sem_repo

LOGGER = logging.getLogger(__name__)


@dataclass
class PurgeStats:
    """Aggregate counters for one ``purge_path`` run."""

    artifact_rows_deleted: int = 0
    artifacts_purged: int = 0  # distinct ids fully purged (exclusive)
    artifacts_shared: int = 0  # distinct ids kept because a duplicate survives
    links_deleted: int = 0
    cache_dirs_removed: int = 0
    reconsolidated: bool = False
    shared_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "artifact_rows_deleted": self.artifact_rows_deleted,
            "artifacts_purged": self.artifacts_purged,
            "artifacts_shared": self.artifacts_shared,
            "links_deleted": self.links_deleted,
            "cache_dirs_removed": self.cache_dirs_removed,
            "reconsolidated": self.reconsolidated,
        }


def purge_path(
    target: str | Path,
    *,
    db_path: str | Path = "data/catalog.sqlite",
    cache_dir: str | Path = "cache",
    config_path: str | Path = "config/sources.yml",
    reconsolidate: bool = True,
    all_sources: bool = False,
) -> PurgeStats:
    """Permanently remove all material related to ``target`` (a file or folder).

    With ``reconsolidate`` (the default) the knowledge graph is rebuilt afterwards
    so objects/relations derived solely from the purged files vanish. ``all_sources``
    runs that rebuild unscoped (legacy behavior); otherwise it is scoped to the
    configured source folders.
    """

    init_db(db_path)
    cache_root = Path(cache_dir)
    stats = PurgeStats()

    with connect(db_path) as conn:
        matched = artifacts_under_path(conn, str(target))
        if not matched:
            LOGGER.info("No indexed artifacts under %s", target)
            return stats

        matched_paths = {row["path"] for row in matched}
        matched_ids = {row["id"] for row in matched}

        # An id is "exclusive" to the target when none of its paths sit outside
        # the matched set; "shared" if a duplicate copy survives elsewhere.
        exclusive_ids: set[str] = set()
        shared_ids: set[str] = set()
        for artifact_id in matched_ids:
            all_paths = {
                r["path"]
                for r in conn.execute(
                    "SELECT path FROM artifacts WHERE id = ?", (artifact_id,)
                )
            }
            if all_paths <= matched_paths:
                exclusive_ids.add(artifact_id)
            else:
                shared_ids.add(artifact_id)

        # Always drop the artifact rows that live under the target.
        stats.artifact_rows_deleted = delete_artifacts_by_paths(
            conn, list(matched_paths)
        )

        # Purge the derived/raw material only for ids that survive nowhere else.
        for artifact_id in exclusive_ids:
            sem_repo.delete_for_artifact(conn, artifact_id)
            stats.links_deleted += link_repo.delete_for_artifact(conn, artifact_id)
            artifact_cache = cache_root / artifact_id
            if artifact_cache.exists():
                shutil.rmtree(artifact_cache, ignore_errors=True)
                stats.cache_dirs_removed += 1

        conn.commit()

    stats.artifacts_purged = len(exclusive_ids)
    stats.artifacts_shared = len(shared_ids)
    stats.shared_ids = sorted(shared_ids)
    if shared_ids:
        LOGGER.warning(
            "%d artifact(s) kept: a duplicate copy lives outside %s",
            len(shared_ids),
            target,
        )

    if reconsolidate:
        source_paths: list[str] | None = None
        if not all_sources:
            try:
                cfg = load_config(config_path)
                source_paths = [source.path for source in cfg.sources]
            except FileNotFoundError:
                source_paths = []
        consolidate(db_path, source_paths=source_paths)
        stats.reconsolidated = True

    return stats


__all__ = ["PurgeStats", "purge_path"]
