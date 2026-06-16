"""CLI tests for the ``catalog compliance`` command group."""

from __future__ import annotations

from catalog.cli import main
from catalog.db import connect


def _base(db):
    return ["--db", db, "compliance"]


_YAML = """
standard:
  name: ISO 27001
  version: "2022"
requirements:
  - clause_ref: A.8.24
    title: Use of cryptography
    text: Rules for cryptography shall be defined.
"""


def test_import_then_consolidate_then_assess(tmp_path, capsys):
    db = str(tmp_path / "c.sqlite")
    catalog = tmp_path / "iso.yml"
    catalog.write_text(_YAML, encoding="utf-8")

    assert main(_base(db) + ["import", str(catalog)]) == 0
    assert "Requirements imported: 1" in capsys.readouterr().out

    assert main(["--db", db, "consolidate"]) == 0
    capsys.readouterr()

    assert main(_base(db) + ["assess"]) == 0
    out = capsys.readouterr().out
    assert "Compliance assessment complete" in out
    assert "Requirements assessed: 1" in out


def test_standards_and_requirements_listing(compliance_db, capsys):
    # compliance_db is already consolidated (which syncs the metadata tables).
    assert main(_base(compliance_db) + ["standards"]) == 0
    assert "GDPR" in capsys.readouterr().out

    assert main(_base(compliance_db) + ["requirements"]) == 0
    out = capsys.readouterr().out
    assert "Art. 32" in out


def test_coverage_gaps_and_prove_flow(compliance_db, capsys):
    assert main(_base(compliance_db) + ["assess"]) == 0
    capsys.readouterr()

    assert main(_base(compliance_db) + ["gaps"]) == 0
    assert "Art. 32" in capsys.readouterr().out

    # Find and approve the satisfied assessment, then prove succeeds.
    with connect(compliance_db) as conn:
        a = conn.execute(
            "SELECT id FROM compliance_assessments WHERE status='SATISFIED'"
        ).fetchone()
    assert main(_base(compliance_db) + ["approve", str(a["id"])]) == 0
    capsys.readouterr()

    assert main(_base(compliance_db) + ["coverage"]) == 0
    assert "50.0%" in capsys.readouterr().out

    assert main(_base(compliance_db) + ["prove", "Art. 32"]) == 0
    out = capsys.readouterr().out
    assert "satisfied by" in out


def test_prove_declines_before_approval(compliance_db, capsys):
    assert main(_base(compliance_db) + ["assess"]) == 0
    capsys.readouterr()
    assert main(_base(compliance_db) + ["prove", "Art. 32"]) == 0
    assert "No supporting evidence found." in capsys.readouterr().out


def test_ask_prove_flag(compliance_db, capsys):
    assert main(_base(compliance_db) + ["assess"]) == 0
    capsys.readouterr()
    assert main(["--db", compliance_db, "ask", "Art. 32", "--prove"]) == 0
    assert "No supporting evidence found." in capsys.readouterr().out
