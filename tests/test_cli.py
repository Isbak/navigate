from catalog.cli import main


def _write_config(tmp_path, docs):
    config = tmp_path / "sources.yml"
    config.write_text(
        f"sources:\n  - path: '{docs}'\n    source_system: 'test'\nexclude: []\n",
        encoding="utf-8",
    )
    return config


def _base_args(tmp_path, config):
    return [
        "--db",
        str(tmp_path / "catalog.sqlite"),
        "--config",
        str(config),
        "--cache",
        str(tmp_path / "cache"),
    ]


def test_scan_command_prints_expected_format(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("a", encoding="utf-8")
    (docs / "b.txt").write_text("b", encoding="utf-8")
    config = _write_config(tmp_path, docs)

    assert main(_base_args(tmp_path, config) + ["scan"]) == 0
    out = capsys.readouterr().out
    assert "Files scanned: 2" in out
    assert "New files: 2" in out
    assert "Modified files: 0" in out
    assert "Deleted files: 0" in out
    assert "Duplicates: 0" in out


def test_stats_command_reads_last_run(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("a", encoding="utf-8")
    config = _write_config(tmp_path, docs)

    main(_base_args(tmp_path, config) + ["scan"])
    capsys.readouterr()
    assert main(_base_args(tmp_path, config) + ["stats"]) == 0
    out = capsys.readouterr().out
    assert "Files scanned: 1" in out
    assert "Indexed artifacts: 1" in out


def test_stats_without_scan_reports_no_runs(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    config = _write_config(tmp_path, docs)
    assert main(_base_args(tmp_path, config) + ["stats"]) == 0
    out = capsys.readouterr().out
    assert "No scans recorded yet" in out


def test_discover_links_and_link_stats_flow(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text(
        "See [repo](https://github.com/acme/repo) and "
        "[wiki](https://contoso.atlassian.net/wiki/spaces/X/pages/1)",
        encoding="utf-8",
    )
    config = _write_config(tmp_path, docs)
    base = _base_args(tmp_path, config)

    assert main(base + ["scan"]) == 0
    capsys.readouterr()

    assert main(base + ["discover-links"]) == 0
    out = capsys.readouterr().out
    assert "Link discovery complete" in out
    assert "Links found: 2" in out
    assert "New links: 2" in out

    assert main(base + ["link-stats"]) == 0
    out = capsys.readouterr().out
    assert "Total links: 2" in out
    assert "github" in out
    assert "confluence" in out


def test_show_links_filters_by_system(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text(
        "[repo](https://github.com/acme/repo)", encoding="utf-8"
    )
    config = _write_config(tmp_path, docs)
    base = _base_args(tmp_path, config)
    main(base + ["scan"])
    main(base + ["discover-links"])
    capsys.readouterr()

    assert main(base + ["show-links", "--system", "github"]) == 0
    out = capsys.readouterr().out
    assert "github.com/acme/repo" in out

    assert main(base + ["show-links", "--system", "teams"]) == 0
    assert "No matching links." in capsys.readouterr().out


def test_export_links_csv(tmp_path, capsys, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text(
        "[repo](https://github.com/acme/repo)", encoding="utf-8"
    )
    config = _write_config(tmp_path, docs)
    base = _base_args(tmp_path, config)
    main(base + ["scan"])
    main(base + ["discover-links"])
    capsys.readouterr()

    monkeypatch.chdir(tmp_path)
    assert main(base + ["export-links-csv"]) == 0
    out = capsys.readouterr().out
    assert "Exported 1 links" in out
    exported = (tmp_path / "exports" / "links.csv").read_text(encoding="utf-8")
    assert "normalized_url" in exported
    assert "github.com/acme/repo" in exported


def _seed_scoped_db(db, keep_dir, drop_dir):
    """Two artifacts (one per folder) with a capability candidate each."""

    from catalog.db import connect, init_db

    init_db(db)
    with connect(db) as conn:
        for folder, art, name in (
            (keep_dir, "doc_keep", "Kept Capability"),
            (drop_dir, "doc_drop", "Dropped Capability"),
        ):
            conn.execute(
                "INSERT INTO artifacts(path,id,filename,file_type,size_bytes,scan_status)"
                " VALUES (?,?,?,?,1,'UNCHANGED')",
                (str(folder / "a.txt"), art, "a.txt", "txt"),
            )
            conn.execute(
                "INSERT INTO candidate_capabilities(artifact_id,name,confidence,"
                "supporting_text,knowledge_type,review_status,model,created_at)"
                " VALUES (?,?,0.9,'q','OBSERVATION','NEW','stub','t')",
                (art, name),
            )
        conn.commit()


def test_consolidate_scopes_to_config_by_default(tmp_path, capsys):
    from catalog.db import connect
    from catalog.knowledge import repository as repo

    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    config = _write_config(tmp_path, keep)  # only 'keep' is configured
    base = _base_args(tmp_path, config)
    db = str(tmp_path / "catalog.sqlite")
    _seed_scoped_db(tmp_path / "catalog.sqlite", keep, drop)

    assert main(base + ["consolidate"]) == 0
    out = capsys.readouterr().out
    assert "configured source folder" in out
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_kept_capability") is not None
        assert repo.get_object(conn, "capability_dropped_capability") is None

    # --all-sources opts out of scoping and brings the dropped object back.
    assert main(base + ["consolidate", "--all-sources"]) == 0
    assert "all sources" in capsys.readouterr().out
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_dropped_capability") is not None


def test_clean_source_command(tmp_path, capsys):
    from catalog.db import connect
    from catalog.knowledge import repository as repo

    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    config = tmp_path / "sources.yml"
    config.write_text(
        f"sources:\n  - path: '{keep}'\n    source_system: 'test'\n"
        f"  - path: '{drop}'\n    source_system: 'test'\nexclude: []\n",
        encoding="utf-8",
    )
    base = _base_args(tmp_path, config)
    db = str(tmp_path / "catalog.sqlite")
    _seed_scoped_db(tmp_path / "catalog.sqlite", keep, drop)

    assert main(base + ["clean-source", "--path", str(drop)]) == 0
    out = capsys.readouterr().out
    assert "Purge complete" in out
    assert "Artifact rows deleted: 1" in out
    assert "Re-consolidated" in out
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_dropped_capability") is None
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM candidate_capabilities WHERE artifact_id='doc_drop'"
            ).fetchone()[0]
            == 0
        )


def test_knowledge_growth_command(governed_db, capsys):
    assert main(["--db", governed_db, "knowledge-growth", "--interval", "month"]) == 0
    out = capsys.readouterr().out
    assert "Knowledge growth (by month):" in out
    assert "objects +" in out


def test_approve_confidence_interval_command(governed_db, capsys):
    from catalog.db import connect

    with connect(governed_db) as conn:
        conn.execute("UPDATE knowledge_objects SET status = 'PROPOSED'")
        conn.execute("UPDATE knowledge_relationships SET review_status = 'PROPOSED'")
        conn.commit()

    assert main([
        "--db",
        governed_db,
        "approve-confidence-interval",
        "--min-confidence",
        "0.0",
        "--max-confidence",
        "1.0",
    ]) == 0
    out = capsys.readouterr().out
    assert "Approved by confidence interval [0.00, 1.00]" in out
    assert "Objects approved:" in out
    assert "Relationships approved:" in out

    with connect(governed_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_objects WHERE status = 'APPROVED'"
        ).fetchone()[0] > 0
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_relationships WHERE review_status = 'APPROVED'"
        ).fetchone()[0] > 0


def _artifact_ids(db):
    from catalog.db import connect

    with connect(db) as conn:
        return [r["id"] for r in conn.execute("SELECT id, path FROM artifacts ORDER BY path")]


def test_extract_path_glob_scopes_to_matching_files(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha", encoding="utf-8")
    (docs / "b.txt").write_text("beta", encoding="utf-8")
    config = _write_config(tmp_path, docs)
    base = _base_args(tmp_path, config)

    assert main(base + ["scan"]) == 0
    capsys.readouterr()

    assert main(base + ["extract", "--path-glob", "*a.txt"]) == 0
    out = capsys.readouterr().out
    assert "mode: fast" in out
    assert "Artifacts processed: 1" in out


def test_extract_artifact_id_scopes_to_one(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha", encoding="utf-8")
    (docs / "b.txt").write_text("beta", encoding="utf-8")
    config = _write_config(tmp_path, docs)
    base = _base_args(tmp_path, config)

    assert main(base + ["scan"]) == 0
    capsys.readouterr()

    ids = _artifact_ids(tmp_path / "catalog.sqlite")
    assert main(base + ["extract", "--artifact-id", ids[0]]) == 0
    out = capsys.readouterr().out
    assert "Artifacts processed: 1" in out


def test_classify_accepts_repeatable_artifact_id():
    from catalog.cli import build_parser

    args = build_parser().parse_args(
        ["classify", "--artifact-id", "doc_a", "--artifact-id", "doc_b"]
    )
    assert args.artifact_id == ["doc_a", "doc_b"]


def test_extract_accepts_mode_flag():
    from catalog.cli import build_parser

    args = build_parser().parse_args(["extract", "--mode", "high-quality"])
    assert args.mode == "high-quality"
