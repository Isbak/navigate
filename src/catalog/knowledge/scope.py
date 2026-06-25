"""Source-folder scoping for the knowledge consolidation pipeline.

Consolidation ("the setup") should only consider material from files that
currently live under a configured source folder. Documents from folders that are
no longer configured keep their raw ``candidate_*`` rows (so they return if the
path is re-added) but must not be consolidated into knowledge objects.

The unit of scope is the content-addressed ``artifact_id``. An id is in scope when

* it has **no** row in the ``artifacts`` table - these are curated imports
  (``import_<standard>``) that have no file path and are always considered; or
* it has at least one non-``DELETED`` ``artifacts`` row whose resolved path lives
  under one of the configured source roots.

Path containment is decided with :func:`os.path.commonpath` rather than a string
prefix so that ``/foo`` does not spuriously match ``/foobar``.

:func:`live_artifact_ids` is the unscoped counterpart: it returns every
non-``DELETED`` file-backed id plus curated imports, without any root filter.
It is used by consolidation when no source-folder scoping is requested so that
``DELETED`` artifacts are always excluded regardless of the ``--all-sources``
flag.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def expand_source_roots(paths: list[str | Path]) -> list[Path]:
    """Expand and resolve configured source paths, mirroring the scanner.

    The scanner stores ``str(path.resolve())`` for every artifact, so scope
    matching has to resolve the configured roots the same way.
    """

    roots: list[Path] = []
    for raw in paths:
        try:
            roots.append(Path(raw).expanduser().resolve())
        except (OSError, RuntimeError):
            # An unresolvable configured path simply contributes no scope.
            continue
    return roots


def _is_under(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            if os.path.commonpath([path, root]) == str(root):
                return True
        except ValueError:
            # Different drives / anchors -> not comparable, not under this root.
            continue
    return False


def in_scope_artifact_ids(
    conn: sqlite3.Connection, roots: list[Path]
) -> set[str]:
    """Return every ``artifact_id`` that consolidation may consider.

    The union of (a) ids backed by a non-``DELETED`` file under a configured root
    and (b) ids with no ``artifacts`` row at all (curated imports). With an empty
    ``roots`` list only the curated ids survive.
    """

    allowed: set[str] = set()

    # File-backed ids: keep an id if any of its (duplicate) paths is live and
    # under a configured root.
    for row in conn.execute(
        "SELECT id, path FROM artifacts WHERE scan_status != 'DELETED'"
    ):
        if row["id"] in allowed:
            continue
        try:
            path = Path(row["path"]).resolve()
        except (OSError, RuntimeError):
            continue
        if _is_under(path, roots):
            allowed.add(row["id"])

    # Curated imports: any candidate-table id that has no artifacts row at all.
    for row in conn.execute(_CURATED_IMPORTS_SQL):
        allowed.add(row["id"])

    return allowed


_CURATED_IMPORTS_SQL = """
    SELECT DISTINCT artifact_id AS id FROM (
        SELECT artifact_id FROM candidate_entities
        UNION SELECT artifact_id FROM candidate_capabilities
        UNION SELECT artifact_id FROM candidate_decisions
        UNION SELECT artifact_id FROM candidate_risks
        UNION SELECT artifact_id FROM candidate_relationships
        UNION SELECT artifact_id FROM candidate_requirements
        UNION SELECT artifact_id FROM candidate_equations
    )
    WHERE artifact_id IS NOT NULL
      AND artifact_id NOT IN (SELECT id FROM artifacts)
"""


def live_artifact_ids(conn: sqlite3.Connection) -> set[str]:
    """Return every artifact id that may be consolidated, ignoring source-folder
    scoping but still honouring ``DELETED`` status.

    This is the unscoped counterpart to :func:`in_scope_artifact_ids`: all
    non-``DELETED`` file-backed ids plus curated imports (ids that have no
    ``artifacts`` row at all) are returned. Used by :func:`consolidate` when
    ``source_paths`` is ``None`` so that the ``--all-sources`` flag disables
    *folder* scoping without accidentally resurrecting ``DELETED`` artifacts.
    """

    allowed: set[str] = set()
    for row in conn.execute(
        "SELECT DISTINCT id FROM artifacts WHERE scan_status != 'DELETED'"
    ):
        allowed.add(row["id"])
    for row in conn.execute(_CURATED_IMPORTS_SQL):
        allowed.add(row["id"])
    return allowed


__all__ = ["expand_source_roots", "in_scope_artifact_ids", "live_artifact_ids"]
