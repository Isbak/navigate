"""Tests for the curated standard catalog importer."""

from __future__ import annotations

from catalog.compliance.importer import import_standard, load_catalog
from catalog.db import connect, init_db

_YAML = """
standard:
  name: ISO 27001
  version: "2022"
  authority: ISO
requirements:
  - clause_ref: A.5.1
    title: Policies for information security
    text: A security policy shall be defined.
    obligation_level: MANDATORY
  - clause_ref: A.8.24
    title: Use of cryptography
    text: Rules for cryptography shall be defined.
"""

_CSV = (
    "standard_name,standard_version,clause_ref,title,text,obligation_level\n"
    "GDPR,2016,Art. 32,Security,shall implement security,MANDATORY\n"
    "GDPR,2016,Art. 30,Records,shall maintain records,RECOMMENDED\n"
)


def test_load_catalog_yaml(tmp_path):
    path = tmp_path / "iso.yml"
    path.write_text(_YAML, encoding="utf-8")
    standard, rows = load_catalog(path)
    assert standard["name"] == "ISO 27001"
    assert len(rows) == 2


def test_import_yaml_writes_candidates(tmp_path):
    db = str(tmp_path / "c.sqlite")
    path = tmp_path / "iso.yml"
    path.write_text(_YAML, encoding="utf-8")

    stats = import_standard(db, path)
    assert stats.requirements_imported == 2
    assert stats.standard_name == "ISO 27001"

    with connect(db) as conn:
        rows = conn.execute(
            "SELECT clause_ref, obligation_level, model, confidence "
            "FROM candidate_requirements ORDER BY clause_ref"
        ).fetchall()
    assert [r["clause_ref"] for r in rows] == ["A.5.1", "A.8.24"]
    assert all(r["model"] == "curated_import" for r in rows)
    assert all(r["confidence"] >= 0.9 for r in rows)


def test_import_csv_writes_candidates(tmp_path):
    db = str(tmp_path / "c.sqlite")
    path = tmp_path / "gdpr.csv"
    path.write_text(_CSV, encoding="utf-8")

    stats = import_standard(db, path)
    assert stats.requirements_imported == 2

    with connect(db) as conn:
        levels = {
            r["clause_ref"]: r["obligation_level"]
            for r in conn.execute(
                "SELECT clause_ref, obligation_level FROM candidate_requirements"
            )
        }
    assert levels == {"Art. 32": "MANDATORY", "Art. 30": "RECOMMENDED"}


def test_reimport_replaces_prior_curated_rows(tmp_path):
    db = str(tmp_path / "c.sqlite")
    path = tmp_path / "iso.yml"
    path.write_text(_YAML, encoding="utf-8")

    import_standard(db, path)
    import_standard(db, path)  # second import should not duplicate

    with connect(db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM candidate_requirements"
        ).fetchone()[0]
    assert count == 2


def test_unknown_format_raises(tmp_path):
    db = str(tmp_path / "c.sqlite")
    init_db(db)
    path = tmp_path / "bad.txt"
    path.write_text("nope", encoding="utf-8")
    try:
        import_standard(db, path)
    except ValueError as exc:
        assert "Unsupported" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
