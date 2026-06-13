"""CLI tests for the RDF export commands (Prompt #7).

Exercises ``rdf-export`` -> ``rdf-validate`` -> ``rdf-stats`` end to end against a
small approved knowledge base, mirroring the success criteria. Fuseki upload is
covered at the library level in ``test_rdf_export.py`` (no network here).
"""

from catalog.cli import main
from catalog.db import connect, init_db
from catalog.knowledge.models import ReviewState
from catalog.knowledge.service import consolidate, review_object


def _base_args(tmp_path):
    return ["--db", str(tmp_path / "catalog.sqlite")]


def _seed_approved(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO candidate_capabilities(
                artifact_id, name, confidence, supporting_text,
                knowledge_type, review_status, model, created_at
            ) VALUES ('doc_a', 'Release Governance', 0.94,
                      'we run release governance', 'OBSERVATION', 'NEW', 'stub', 't')
            """
        )
        conn.commit()
    consolidate(db)
    review_object(db, "capability_release_governance", ReviewState.APPROVED.value)
    return db


def test_rdf_export_validate_stats_flow(tmp_path, capsys):
    _seed_approved(tmp_path)
    base = _base_args(tmp_path)
    out = str(tmp_path / "rdf")

    assert main(base + ["rdf-export", "--out", out]) == 0
    export_out = capsys.readouterr().out
    assert "RDF export complete" in export_out
    assert "Objects exported: 1" in export_out
    assert "knowledge.ttl" in export_out

    assert main(base + ["rdf-validate", "--out", out]) == 0
    validate_out = capsys.readouterr().out
    assert "All files valid." in validate_out
    assert "knowledge.ttl" in validate_out

    assert main(base + ["rdf-stats"]) == 0
    stats_out = capsys.readouterr().out
    assert "Objects exported: 1" in stats_out


def test_rdf_validate_without_export_reports_missing(tmp_path, capsys):
    base = _base_args(tmp_path)
    assert main(base + ["rdf-validate", "--out", str(tmp_path / "none")]) == 0
    out = capsys.readouterr().out
    assert "No RDF files found" in out
