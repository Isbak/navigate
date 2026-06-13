from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

DEFAULT_DB_PATH = Path("data/catalog.sqlite")

# ``path`` is the natural identity of a file location, so it is the primary key.
# ``id`` is content-addressed (doc_<first 12 sha256 chars>) and is therefore the
# SAME for byte-identical files: duplicates share an id, which is exactly how we
# detect them. ``id`` is indexed but intentionally not UNIQUE.
SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS artifacts(
  path TEXT PRIMARY KEY,
  id TEXT NOT NULL,
  filename TEXT NOT NULL,
  file_type TEXT NOT NULL,
  size_bytes INTEGER,
  created_at TEXT,
  modified_at TEXT,
  sha256 TEXT,
  source_system TEXT DEFAULT 'local_laptop',
  scan_status TEXT DEFAULT 'RAW',
  first_seen_at TEXT,
  last_scanned_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_sha256 ON artifacts(sha256);
CREATE INDEX IF NOT EXISTS idx_artifacts_id ON artifacts(id);
CREATE INDEX IF NOT EXISTS idx_artifacts_status ON artifacts(scan_status);
CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_path TEXT NOT NULL,
  target_url TEXT NOT NULL,
  anchor_text TEXT,
  target_system TEXT,
  target_type TEXT,
  discovered_at TEXT,
  FOREIGN KEY(source_path) REFERENCES artifacts(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_path);
CREATE TABLE IF NOT EXISTS scan_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT,
  finished_at TEXT,
  files_scanned INTEGER DEFAULT 0,
  new_files INTEGER DEFAULT 0,
  changed_files INTEGER DEFAULT 0,
  unchanged_files INTEGER DEFAULT 0,
  duplicate_files INTEGER DEFAULT 0,
  deleted_files INTEGER DEFAULT 0
);
"""

# Columns expected on a current ``artifacts`` table; a mismatch triggers a
# rebuild of the (regenerable) local index.
_EXPECTED_ARTIFACT_COLUMNS = {
    "path",
    "id",
    "filename",
    "file_type",
    "size_bytes",
    "created_at",
    "modified_at",
    "sha256",
    "source_system",
    "scan_status",
    "first_seen_at",
    "last_scanned_at",
}


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _needs_rebuild(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='artifacts'"
    ).fetchone()
    if row is None:
        return False  # fresh database; CREATE statements handle it
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(artifacts)")}
    return columns != _EXPECTED_ARTIFACT_COLUMNS


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Create the schema, rebuilding the local index if it predates this layout.

    The catalog is a regenerable index over source files, so when an older
    schema is detected we drop and recreate rather than attempt an in-place
    migration. Source documents are never touched.
    """

    with connect(db_path) as conn:
        if _needs_rebuild(conn):
            conn.executescript(
                "DROP TABLE IF EXISTS links;"
                "DROP TABLE IF EXISTS scan_runs;"
                "DROP TABLE IF EXISTS artifacts;"
            )
        conn.executescript(SCHEMA)


def existing_artifacts(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Return the currently indexed artifacts keyed by source path."""

    return {row["path"]: row for row in conn.execute("SELECT * FROM artifacts")}


def upsert_artifact(conn: sqlite3.Connection, artifact: dict) -> None:
    conn.execute(
        """
        INSERT INTO artifacts(path,id,filename,file_type,size_bytes,created_at,modified_at,sha256,source_system,scan_status,first_seen_at,last_scanned_at)
        VALUES(:path,:id,:filename,:file_type,:size_bytes,:created_at,:modified_at,:sha256,:source_system,:scan_status,:first_seen_at,:last_scanned_at)
        ON CONFLICT(path) DO UPDATE SET
          id=excluded.id, filename=excluded.filename, file_type=excluded.file_type, size_bytes=excluded.size_bytes,
          created_at=excluded.created_at, modified_at=excluded.modified_at, sha256=excluded.sha256,
          source_system=excluded.source_system, scan_status=excluded.scan_status, last_scanned_at=excluded.last_scanned_at
        """,
        artifact,
    )


def mark_deleted(conn: sqlite3.Connection, path: str, scanned_at: str) -> None:
    conn.execute(
        "UPDATE artifacts SET scan_status='DELETED', last_scanned_at=? WHERE path=?",
        (scanned_at, path),
    )


def record_scan_run(conn: sqlite3.Connection, started_at: str, finished_at: str, stats: dict) -> int:
    cur = conn.execute(
        """
        INSERT INTO scan_runs(started_at,finished_at,files_scanned,new_files,changed_files,unchanged_files,duplicate_files,deleted_files)
        VALUES(:started_at,:finished_at,:files_scanned,:new_files,:changed_files,:unchanged_files,:duplicate_files,:deleted_files)
        """,
        {"started_at": started_at, "finished_at": finished_at, **stats},
    )
    return int(cur.lastrowid)


def latest_scan_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()


def replace_links(conn: sqlite3.Connection, source_path: str, links: Iterable[dict]) -> None:
    conn.execute("DELETE FROM links WHERE source_path = ?", (source_path,))
    conn.executemany(
        """INSERT INTO links(source_path,target_url,anchor_text,target_system,target_type,discovered_at)
           VALUES(:source_path,:target_url,:anchor_text,:target_system,:target_type,:discovered_at)""",
        list(links),
    )
