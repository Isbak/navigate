"""CLI tests for the ``catalog governance`` command group (Prompt #10)."""

import json

from catalog.cli import main


def _base(governed_db):
    return ["--db", governed_db, "governance"]


def test_governance_scan(governed_db, capsys):
    assert main(_base(governed_db) + ["scan"]) == 0
    out = capsys.readouterr().out
    assert "Governance scan complete" in out
    assert "Objects seen" in out


def test_governance_dashboard(governed_db, capsys):
    assert main(_base(governed_db) + ["dashboard"]) == 0
    out = capsys.readouterr().out
    assert "Knowledge Health Dashboard" in out
    assert "Knowledge objects:" in out
    assert "Average quality:" in out


def test_governance_review_queue(governed_db, capsys):
    assert main(_base(governed_db) + ["review-queue"]) == 0
    out = capsys.readouterr().out
    assert "Review queue" in out
    assert "Release Governance" in out


def test_governance_quality(governed_db, capsys):
    assert main(_base(governed_db) + ["quality"]) == 0
    out = capsys.readouterr().out
    assert "Quality scores" in out
    assert "Release Governance" in out


def test_governance_orphaned(governed_db, capsys):
    assert main(_base(governed_db) + ["orphaned"]) == 0
    out = capsys.readouterr().out
    assert "Objects without owner" in out


def test_governance_alerts(governed_db, capsys):
    assert main(_base(governed_db) + ["alerts"]) == 0
    out = capsys.readouterr().out
    assert "Open alerts" in out


def test_governance_approve_and_history(governed_db, capsys):
    assert main(_base(governed_db) + ["approve", "capability_release_governance"]) == 0
    assert "APPROVED" in capsys.readouterr().out

    assert main(_base(governed_db) + ["history", "capability_release_governance"]) == 0
    out = capsys.readouterr().out
    assert "History: Release Governance" in out
    assert "Review state: APPROVED" in out
    assert "review_changed" in out


def test_governance_assign_owner_and_owners(governed_db, capsys):
    assert main(
        _base(governed_db)
        + ["assign-owner", "capability_release_governance", "Team", "Test & Release Team"]
    ) == 0
    capsys.readouterr()
    assert main(_base(governed_db) + ["owners"]) == 0
    out = capsys.readouterr().out
    assert "Test & Release Team" in out


def test_governance_archive(governed_db, capsys):
    assert main(_base(governed_db) + ["archive", "capability_release_governance"]) == 0
    out = capsys.readouterr().out
    assert "ARCHIVED" in out


def test_governance_domains(governed_db, capsys):
    assert main(_base(governed_db) + ["domains"]) == 0
    out = capsys.readouterr().out
    assert "Domain governance" in out
    assert "Test & Release" in out


def test_governance_stale(governed_db, capsys):
    assert main(_base(governed_db) + ["stale"]) == 0
    assert "Stale knowledge" in capsys.readouterr().out


def test_governance_changes(governed_db, capsys):
    assert main(_base(governed_db) + ["changes"]) == 0
    out = capsys.readouterr().out
    assert "Recent changes" in out
    assert "object_added" in out


def test_governance_export(governed_db, tmp_path, capsys):
    out_dir = str(tmp_path / "gov")
    assert main(_base(governed_db) + ["--out", out_dir, "export"]) == 0
    capsys.readouterr()
    health = json.loads((tmp_path / "gov" / "knowledge_health.json").read_text())
    assert "average_quality" in health
    quality = json.loads((tmp_path / "gov" / "quality_report.json").read_text())
    assert isinstance(quality, list)
    changelog = json.loads((tmp_path / "gov" / "change_log.json").read_text())
    assert isinstance(changelog, list)


def test_governance_unknown_object_message(governed_db, capsys):
    assert main(_base(governed_db) + ["approve", "capability_missing"]) == 0
    assert "No knowledge object" in capsys.readouterr().out
