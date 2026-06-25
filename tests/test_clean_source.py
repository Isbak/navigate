"""Tests for the ``clean-source`` purge (catalog.maintenance.service)."""

from pathlib import Path

from catalog.db import connect, init_db
from catalog.knowledge import repository as repo
from catalog.knowledge.service import consolidate
from catalog.maintenance import purge_path


def _insert_artifact(conn, *, path, artifact_id, scan_status="UNCHANGED"):
    conn.execute(
        """
        INSERT INTO artifacts(
            path, id, filename, file_type, size_bytes, scan_status
        ) VALUES (?, ?, ?, 'txt', 1, ?)
        """,
        (str(path), artifact_id, Path(path).name, scan_status),
    )


def _seed_capability(conn, artifact_id, name):
    conn.execute(
        """
        INSERT INTO candidate_capabilities(
            artifact_id, name, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, 0.9, 'q', 'OBSERVATION', 'NEW', 'stub', 't')
        """,
        (artifact_id, name),
    )


def _seed_link(conn, artifact_id):
    conn.execute(
        """
        INSERT INTO links(
            source_artifact_id, raw_url, normalized_url, anchor_text,
            target_system, target_type, link_kind, discovered_at,
            last_seen_at, status
        ) VALUES (?, 'http://x', 'http://x', '', 'web', 'page', 'ref', 't', 't', 'ACTIVE')
        """,
        (artifact_id,),
    )


def _write_config(tmp_path, *folders):
    cfg = tmp_path / "sources.yml"
    lines = ["sources:"]
    for folder in folders:
        lines.append(f"  - path: '{folder}'\n    source_system: 'test'")
    cfg.write_text("\n".join(lines) + "\nexclude: []\n", encoding="utf-8")
    return cfg


def test_purge_folder_removes_all_material(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    folder = tmp_path / "old"
    folder.mkdir()
    (cache / "doc_old").mkdir(parents=True)
    (cache / "doc_old" / "extracted.txt").write_text("x", encoding="utf-8")
    cfg = _write_config(tmp_path)  # empty sources

    init_db(db)
    with connect(db) as conn:
        _insert_artifact(conn, path=folder / "a.txt", artifact_id="doc_old")
        _seed_capability(conn, "doc_old", "Old Capability")
        _seed_link(conn, "doc_old")
        conn.commit()
    consolidate(db, source_paths=None)  # object exists pre-purge
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_old_capability") is not None

    stats = purge_path(
        folder, db_path=db, cache_dir=cache, config_path=cfg, reconsolidate=True
    )

    assert stats.artifact_rows_deleted == 1
    assert stats.artifacts_purged == 1
    assert stats.links_deleted == 1
    assert stats.cache_dirs_removed == 1
    assert stats.reconsolidated is True
    assert not (cache / "doc_old").exists()
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_old_capability") is None
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM candidate_capabilities WHERE artifact_id='doc_old'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM artifacts WHERE id='doc_old'").fetchone()[0]
            == 0
        )


def test_purge_keeps_shared_duplicate(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    target = tmp_path / "target"
    other = tmp_path / "other"
    target.mkdir()
    other.mkdir()
    cfg = _write_config(tmp_path, other)

    init_db(db)
    with connect(db) as conn:
        # Same content id under two folders; only one is being purged.
        _insert_artifact(conn, path=target / "dup.txt", artifact_id="doc_dup")
        _insert_artifact(conn, path=other / "dup.txt", artifact_id="doc_dup")
        _seed_capability(conn, "doc_dup", "Shared Capability")
        conn.commit()

    stats = purge_path(
        target, db_path=db, cache_dir=cache, config_path=cfg, reconsolidate=True
    )

    # The target row is deleted, but the shared candidate material is kept.
    assert stats.artifact_rows_deleted == 1
    assert stats.artifacts_purged == 0
    assert stats.artifacts_shared == 1
    assert "doc_dup" in stats.shared_ids
    with connect(db) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM candidate_capabilities WHERE artifact_id='doc_dup'"
            ).fetchone()[0]
            == 1
        )
        # The surviving copy is still under a configured source, so the object lives.
        assert repo.get_object(conn, "capability_shared_capability") is not None


def test_purge_single_file_and_no_reconsolidate(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    folder = tmp_path / "docs"
    folder.mkdir()
    cfg = _write_config(tmp_path, folder)

    init_db(db)
    with connect(db) as conn:
        _insert_artifact(conn, path=folder / "keep.txt", artifact_id="doc_keep")
        _insert_artifact(conn, path=folder / "gone.txt", artifact_id="doc_gone")
        _seed_capability(conn, "doc_keep", "Keep Capability")
        _seed_capability(conn, "doc_gone", "Gone Capability")
        conn.commit()

    stats = purge_path(
        folder / "gone.txt",
        db_path=db,
        cache_dir=cache,
        config_path=cfg,
        reconsolidate=False,
    )

    assert stats.artifact_rows_deleted == 1
    assert stats.reconsolidated is False
    with connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM candidate_capabilities WHERE artifact_id='doc_gone'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM candidate_capabilities WHERE artifact_id='doc_keep'"
        ).fetchone()[0] == 1


def test_purge_nonexistent_path_is_noop(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cfg = _write_config(tmp_path)
    init_db(db)
    stats = purge_path(
        tmp_path / "nope", db_path=db, cache_dir=tmp_path / "cache", config_path=cfg
    )
    assert stats.artifact_rows_deleted == 0
    assert stats.reconsolidated is False


def _seed_requirement(conn, artifact_id, standard_name, clause_ref, text):
    conn.execute(
        """
        INSERT INTO candidate_requirements(
            artifact_id, standard_name, standard_version, clause_ref, title,
            requirement_text, obligation_level, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, '1.0', ?, '', ?, 'MANDATORY', 0.9, ?,
                  'OBSERVATION', 'NEW', 'stub', 't')
        """,
        (artifact_id, standard_name, clause_ref, text, text),
    )


def test_purge_removes_all_derived_data(tmp_path):
    """Full end-to-end: every layer of derived data is gone after purge + reconsolidate."""
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    folder = tmp_path / "docs"
    folder.mkdir()
    (cache / "doc_pol").mkdir(parents=True)
    (cache / "doc_pol" / "extracted.txt").write_text("x", encoding="utf-8")
    cfg = _write_config(tmp_path)  # empty sources → scoped reconsolidate keeps nothing

    init_db(db)
    with connect(db) as conn:
        _insert_artifact(conn, path=folder / "policy.txt", artifact_id="doc_pol")
        _seed_requirement(conn, "doc_pol", "ISO 27001", "A.9.1", "Access control")
        _seed_capability(conn, "doc_pol", "Access Management")
        _seed_link(conn, "doc_pol")
        conn.commit()

    # Consolidate so knowledge objects, relationships, and compliance metadata exist.
    consolidate(db, source_paths=None)
    with connect(db) as conn:
        assert repo.get_object(conn, "standard_iso_27001") is not None
        assert conn.execute("SELECT COUNT(*) FROM knowledge_relationships").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM compliance_standards").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM compliance_requirements").fetchone()[0] > 0

    # Purge and reconsolidate.
    stats = purge_path(folder, db_path=db, cache_dir=cache, config_path=cfg, reconsolidate=True)
    assert stats.artifact_rows_deleted == 1
    assert stats.artifacts_purged == 1

    with connect(db) as conn:
        # Artifact row gone.
        assert conn.execute("SELECT COUNT(*) FROM artifacts WHERE id='doc_pol'").fetchone()[0] == 0
        # Raw candidate rows gone.
        assert conn.execute("SELECT COUNT(*) FROM candidate_requirements WHERE artifact_id='doc_pol'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM candidate_capabilities WHERE artifact_id='doc_pol'").fetchone()[0] == 0
        # Links gone.
        assert conn.execute("SELECT COUNT(*) FROM links WHERE source_artifact_id='doc_pol'").fetchone()[0] == 0
        # Knowledge objects gone.
        assert repo.get_object(conn, "standard_iso_27001") is None
        assert repo.get_object(conn, "capability_access_management") is None
        # Graph relationships gone.
        assert conn.execute("SELECT COUNT(*) FROM knowledge_relationships").fetchone()[0] == 0
        # Evidence and mentions gone.
        assert conn.execute("SELECT COUNT(*) FROM knowledge_evidence").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_mentions").fetchone()[0] == 0
        # Compliance metadata gone.
        assert conn.execute("SELECT COUNT(*) FROM compliance_standards").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM compliance_requirements").fetchone()[0] == 0
    # Extraction cache directory removed.
    assert not (cache / "doc_pol").exists()
