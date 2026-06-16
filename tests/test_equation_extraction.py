"""End-to-end tests for curated equation import -> consolidate -> approve."""

from __future__ import annotations

from catalog.compliance.importer import import_standard, load_equations
from catalog.compliance import repository as comp_repo
from catalog.db import connect
from catalog.knowledge.ids import equation_display_name, object_id
from catalog.knowledge.service import consolidate, review_object
from catalog.knowledge.models import ReviewState


_YAML = """
standard:
  name: EN 1992-1-1
  version: "2004"
  authority: CEN
requirements:
  - clause_ref: 6.2.2(1)
    title: Design shear resistance
    text: The design shear resistance shall be taken as VRd,c.
equations:
  - clause_ref: 6.2.2(1)
    symbol: V_Rd_c
    title: Design shear resistance
    expression: C_Rd_c * k * (100 * rho_l * f_ck) ** (1 / 3) * b_w * d
    variables:
      - symbol: f_ck
        description: characteristic strength
        unit: MPa
      - symbol: d
        description: effective depth
        unit: mm
  - clause_ref: 6.2.2(1)
    symbol: k
    title: Size effect factor
    expression: min(1 + sqrt(200 / d), 2.0)
  - clause_ref: 6.2.2(1)
    symbol: bad
    title: Disallowed formula
    expression: __import__('os').system('boom')
"""


def _import(tmp_path):
    db = str(tmp_path / "c.sqlite")
    path = tmp_path / "ec2.yml"
    path.write_text(_YAML, encoding="utf-8")
    stats = import_standard(db, path)
    return db, stats


def test_load_equations_reads_yaml(tmp_path):
    path = tmp_path / "ec2.yml"
    path.write_text(_YAML, encoding="utf-8")
    equations = load_equations(path)
    assert {e["symbol"] for e in equations} == {"V_Rd_c", "k", "bad"}


def test_import_writes_validated_candidate_equations(tmp_path):
    db, stats = _import(tmp_path)
    assert stats.equations_imported == 3
    with connect(db) as conn:
        rows = {
            r["symbol"]: r
            for r in conn.execute(
                "SELECT symbol, valid, python_code, model, confidence "
                "FROM candidate_equations"
            )
        }
    # Valid formulas pass; the import payload is kept but flagged invalid.
    assert rows["V_Rd_c"]["valid"] == 1
    assert rows["k"]["valid"] == 1
    assert rows["bad"]["valid"] == 0
    assert "def V_Rd_c" in rows["V_Rd_c"]["python_code"]
    assert all(r["model"] == "curated_import" for r in rows.values())


def test_consolidate_creates_equation_objects_and_links(tmp_path):
    db, _ = _import(tmp_path)
    consolidate(db)

    eq_id = object_id("Equation", equation_display_name("EN 1992-1-1", "V_Rd_c", ""))
    with connect(db) as conn:
        obj = conn.execute(
            "SELECT object_type, status FROM knowledge_objects WHERE id = ?", (eq_id,)
        ).fetchone()
        assert obj is not None
        assert obj["object_type"] == "Equation"

        # The enriched compliance_equations row carries the machine-readable payload.
        eq = comp_repo.get_equation(conn, eq_id)
        assert eq is not None
        assert eq["symbol"] == "V_Rd_c"
        assert "def V_Rd_c" in eq["python_code"]
        assert eq["standard_object_id"] == object_id("Standard", "EN 1992-1-1")
        # Linked to the requirement on the same clause.
        assert eq["requirement_object_id"] == object_id(
            "Requirement", "EN 1992-1-1 6.2.2(1)"
        )

        # mandated_by (Equation -> Standard) and specifies (Requirement -> Equation).
        preds = {
            (r["source_object"], r["predicate"], r["target_object"])
            for r in conn.execute("SELECT * FROM knowledge_relationships")
        }
    assert (eq_id, "mandated_by", object_id("Standard", "EN 1992-1-1")) in preds
    assert (
        object_id("Requirement", "EN 1992-1-1 6.2.2(1)"),
        "specifies",
        eq_id,
    ) in preds


def test_equation_object_approval_survives_reconsolidate(tmp_path):
    db, _ = _import(tmp_path)
    consolidate(db)
    eq_id = object_id("Equation", equation_display_name("EN 1992-1-1", "V_Rd_c", ""))

    assert review_object(db, eq_id, ReviewState.APPROVED.value)
    consolidate(db)  # non-force rebuild must preserve the human decision

    with connect(db) as conn:
        status = conn.execute(
            "SELECT status FROM knowledge_objects WHERE id = ?", (eq_id,)
        ).fetchone()["status"]
    assert status == ReviewState.APPROVED.value
