import sqlite3

from catalog.scanner import scan


def test_scan_indexes_text_file_and_links(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "note.md"
    source.write_text("# Note\nSee [repo](https://github.com/acme/repo)", encoding="utf-8")
    config = tmp_path / "sources.yml"
    config.write_text(f"sources:\n  - path: '{docs}'\n    source_system: 'test'\nexclude: []\n", encoding="utf-8")
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"

    assert scan(config, db, cache) == 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    artifact = conn.execute("SELECT * FROM artifacts").fetchone()
    assert artifact["filename"] == "note.md"
    assert artifact["source_system"] == "test"
    assert artifact["id"].startswith("doc_")
    assert (cache / artifact["id"] / "extracted.txt").read_text(encoding="utf-8").startswith("# Note")
    link = conn.execute("SELECT * FROM links").fetchone()
    assert link["target_system"] == "github"


def test_scan_records_duplicate_content_with_distinct_ids(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("same", encoding="utf-8")
    (docs / "b.txt").write_text("same", encoding="utf-8")
    config = tmp_path / "sources.yml"
    config.write_text(f"sources:\n  - path: '{docs}'\nexclude: []\n", encoding="utf-8")
    db = tmp_path / "catalog.sqlite"

    assert scan(config, db, tmp_path / "cache") == 2
    conn = sqlite3.connect(db)
    duplicates = conn.execute("SELECT sha256, COUNT(*) FROM artifacts GROUP BY sha256 HAVING COUNT(*) = 2").fetchall()
    assert len(duplicates) == 1
