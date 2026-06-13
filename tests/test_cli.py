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
