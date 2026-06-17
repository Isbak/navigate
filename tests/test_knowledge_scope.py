"""Unit tests for source-folder scoping of consolidation.

``in_scope_artifact_ids`` decides which artifacts consolidation may consider: a
document is in scope when it lives (non-DELETED) under a configured source root,
or when it is a curated import with no ``artifacts`` row at all.
"""

from pathlib import Path

from catalog.db import connect, init_db
from catalog.knowledge.scope import expand_source_roots, in_scope_artifact_ids


def _insert_artifact(conn, *, path, artifact_id, scan_status="UNCHANGED"):
    conn.execute(
        """
        INSERT INTO artifacts(
            path, id, filename, file_type, size_bytes, scan_status
        ) VALUES (?, ?, ?, 'txt', 1, ?)
        """,
        (str(path), artifact_id, Path(path).name, scan_status),
    )


def _seed_candidate(conn, artifact_id, name="thing"):
    conn.execute(
        """
        INSERT INTO candidate_capabilities(
            artifact_id, name, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, 0.9, 'q', 'OBSERVATION', 'NEW', 'stub', 't')
        """,
        (artifact_id, name),
    )


def test_in_scope_includes_only_artifacts_under_roots(tmp_path):
    db = tmp_path / "c.sqlite"
    init_db(db)
    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    with connect(db) as conn:
        _insert_artifact(conn, path=keep / "a.txt", artifact_id="doc_keep")
        _insert_artifact(conn, path=drop / "b.txt", artifact_id="doc_drop")
        conn.commit()
        allowed = in_scope_artifact_ids(conn, expand_source_roots([str(keep)]))
    assert "doc_keep" in allowed
    assert "doc_drop" not in allowed


def test_curated_imports_are_always_in_scope(tmp_path):
    db = tmp_path / "c.sqlite"
    init_db(db)
    with connect(db) as conn:
        # No artifacts row for this id: it is a curated import.
        _seed_candidate(conn, "import_iso27001")
        conn.commit()
        allowed = in_scope_artifact_ids(conn, expand_source_roots([str(tmp_path)]))
    assert "import_iso27001" in allowed
    # Even with an empty scope, curated imports survive.
    with connect(db) as conn:
        allowed = in_scope_artifact_ids(conn, [])
    assert allowed == {"import_iso27001"}


def test_deleted_rows_are_out_of_scope(tmp_path):
    db = tmp_path / "c.sqlite"
    init_db(db)
    src = tmp_path / "src"
    src.mkdir()
    with connect(db) as conn:
        _insert_artifact(
            conn, path=src / "gone.txt", artifact_id="doc_gone", scan_status="DELETED"
        )
        conn.commit()
        allowed = in_scope_artifact_ids(conn, expand_source_roots([str(src)]))
    assert "doc_gone" not in allowed


def test_duplicate_id_in_scope_if_any_copy_is(tmp_path):
    db = tmp_path / "c.sqlite"
    init_db(db)
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    with connect(db) as conn:
        # Same content id under two paths; only one is in scope.
        _insert_artifact(conn, path=inside / "x.txt", artifact_id="doc_dup")
        _insert_artifact(conn, path=outside / "x.txt", artifact_id="doc_dup")
        conn.commit()
        allowed = in_scope_artifact_ids(conn, expand_source_roots([str(inside)]))
    assert "doc_dup" in allowed


def test_sibling_prefix_does_not_match(tmp_path):
    db = tmp_path / "c.sqlite"
    init_db(db)
    root = tmp_path / "foo"
    sibling = tmp_path / "foobar"
    root.mkdir()
    sibling.mkdir()
    with connect(db) as conn:
        _insert_artifact(conn, path=sibling / "a.txt", artifact_id="doc_sibling")
        conn.commit()
        allowed = in_scope_artifact_ids(conn, expand_source_roots([str(root)]))
    assert "doc_sibling" not in allowed
