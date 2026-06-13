from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

DEFAULT_DB_PATH = Path("data/catalog.sqlite")

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS artifacts(
  id TEXT PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  filename TEXT NOT NULL,
  file_type TEXT NOT NULL,
  size_bytes INTEGER,
  created_at TEXT,
  modified_at TEXT,
  sha256 TEXT,
  source_system TEXT DEFAULT 'local_laptop',
  scan_status TEXT DEFAULT 'raw',
  last_scanned_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_sha256 ON artifacts(sha256);
CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_artifact_id TEXT NOT NULL,
  target_url TEXT NOT NULL,
  anchor_text TEXT,
  target_system TEXT,
  target_type TEXT,
  discovered_at TEXT,
  FOREIGN KEY(source_artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_artifact_id);
"""


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_artifact(conn: sqlite3.Connection, artifact: dict) -> None:
    conn.execute(
        """
        INSERT INTO artifacts(id,path,filename,file_type,size_bytes,created_at,modified_at,sha256,source_system,scan_status,last_scanned_at)
        VALUES(:id,:path,:filename,:file_type,:size_bytes,:created_at,:modified_at,:sha256,:source_system,:scan_status,:last_scanned_at)
        ON CONFLICT(path) DO UPDATE SET
          id=excluded.id, filename=excluded.filename, file_type=excluded.file_type, size_bytes=excluded.size_bytes,
          created_at=excluded.created_at, modified_at=excluded.modified_at, sha256=excluded.sha256,
          source_system=excluded.source_system, scan_status=excluded.scan_status, last_scanned_at=excluded.last_scanned_at
        """,
        artifact,
    )


def replace_links(conn: sqlite3.Connection, artifact_id: str, links: Iterable[dict]) -> None:
    conn.execute("DELETE FROM links WHERE source_artifact_id = ?", (artifact_id,))
    conn.executemany(
        """INSERT INTO links(source_artifact_id,target_url,anchor_text,target_system,target_type,discovered_at)
           VALUES(:source_artifact_id,:target_url,:anchor_text,:target_system,:target_type,:discovered_at)""",
        list(links),
    )
