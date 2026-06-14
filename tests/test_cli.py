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


def test_knowledge_growth_command(governed_db, capsys):
    assert main(["--db", governed_db, "knowledge-growth", "--interval", "month"]) == 0
    out = capsys.readouterr().out
    assert "Knowledge growth (by month):" in out
    assert "objects +" in out
