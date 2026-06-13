import json
import sqlite3

from catalog.events import ScanStatus
from catalog.scanner import Scanner, scan


def _write_config(tmp_path, docs, exclude="[]", source_system="test"):
    config = tmp_path / "sources.yml"
    config.write_text(
        f"sources:\n  - path: '{docs}'\n    source_system: '{source_system}'\nexclude: {exclude}\n",
        encoding="utf-8",
    )
    return config


def test_scan_indexes_text_file_and_caches_links(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text(
        "# Note\nSee [repo](https://github.com/acme/repo)", encoding="utf-8"
    )
    config = _write_config(tmp_path, docs)
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"

    stats = scan(config, db, cache)
    assert stats.files_scanned == 1
    assert stats.new_files == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    artifact = conn.execute("SELECT * FROM artifacts").fetchone()
    assert artifact["filename"] == "note.md"
    assert artifact["source_system"] == "test"
    assert artifact["id"].startswith("doc_")
    assert artifact["scan_status"] == "RAW"

    artifact_cache = cache / artifact["id"]
    assert (artifact_cache / "extracted.txt").read_text(encoding="utf-8").startswith("# Note")

    # Extraction writes raw links to the cache; the DB is populated separately by
    # the discovery layer, so the links table is still empty after a scan.
    raw_links = json.loads((artifact_cache / "links.json").read_text(encoding="utf-8"))
    assert {link["raw_url"] for link in raw_links} == {"https://github.com/acme/repo"}
    metadata = json.loads((artifact_cache / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["artifact_id"] == artifact["id"]
    assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0


def test_duplicate_content_shares_id_and_is_flagged(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("same", encoding="utf-8")
    (docs / "b.txt").write_text("same", encoding="utf-8")
    config = _write_config(tmp_path, docs)
    db = tmp_path / "catalog.sqlite"

    stats = scan(config, db, tmp_path / "cache")
    assert stats.files_scanned == 2
    assert stats.duplicate_files == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT path, id, scan_status FROM artifacts ORDER BY path").fetchall()
    # Both files map to the same content-addressed id.
    assert rows[0]["id"] == rows[1]["id"]
    statuses = {r["scan_status"] for r in rows}
    assert "DUPLICATE" in statuses


def test_change_detection_new_changed_unchanged_deleted(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    keep = docs / "keep.txt"
    keep.write_text("v1", encoding="utf-8")
    gone = docs / "gone.txt"
    gone.write_text("temporary", encoding="utf-8")
    config = _write_config(tmp_path, docs)
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"

    first = scan(config, db, cache)
    assert first.new_files == 2

    # Modify one, delete one, add one.
    keep.write_text("v2-changed", encoding="utf-8")
    gone.unlink()
    (docs / "fresh.txt").write_text("brand new", encoding="utf-8")

    second = scan(config, db, cache)
    assert second.new_files == 1
    assert second.changed_files == 1
    assert second.unchanged_files == 0
    assert second.deleted_files == 1
    assert second.files_scanned == 2

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    statuses = {
        row["filename"]: row["scan_status"]
        for row in conn.execute("SELECT filename, scan_status FROM artifacts")
    }
    assert statuses["keep.txt"] == "CHANGED"
    assert statuses["gone.txt"] == "DELETED"
    assert statuses["fresh.txt"] == "RAW"


def test_unchanged_file_reports_unchanged(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "stable.txt").write_text("constant", encoding="utf-8")
    config = _write_config(tmp_path, docs)
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"

    scan(config, db, cache)
    second = scan(config, db, cache)
    assert second.unchanged_files == 1
    assert second.new_files == 0
    assert second.changed_files == 0


def test_exclude_patterns_are_respected(tmp_path):
    docs = tmp_path / "docs"
    (docs / "sub").mkdir(parents=True)
    (docs / "keep.txt").write_text("keep", encoding="utf-8")
    (docs / "sub" / "skip.txt").write_text("skip", encoding="utf-8")
    config = _write_config(tmp_path, docs, exclude="['**/sub/**']")
    db = tmp_path / "catalog.sqlite"

    stats = scan(config, db, tmp_path / "cache")
    assert stats.files_scanned == 1


def test_unsupported_extensions_are_ignored(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "doc.txt").write_text("ok", encoding="utf-8")
    (docs / "image.png").write_bytes(b"\x89PNG")
    config = _write_config(tmp_path, docs)
    db = tmp_path / "catalog.sqlite"

    stats = scan(config, db, tmp_path / "cache")
    assert stats.files_scanned == 1


def test_scan_records_run_statistics(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("a", encoding="utf-8")
    config = _write_config(tmp_path, docs)
    db = tmp_path / "catalog.sqlite"

    scan(config, db, tmp_path / "cache")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    run = conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run["files_scanned"] == 1
    assert run["new_files"] == 1
    assert run["finished_at"] is not None


def test_scanner_publishes_events_to_subscribers(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("hello", encoding="utf-8")
    config = _write_config(tmp_path, docs)
    db = tmp_path / "catalog.sqlite"

    received = []
    scanner = Scanner(db_path=db)
    scanner.event_bus.subscribe(lambda event: received.append(event.status))
    scanner.scan(config)

    assert ScanStatus.RAW in received
