"""Tests for the safe, non-executing equation validator/structurer."""

from __future__ import annotations

import json

from catalog.semantic.equation_ast import (
    EquationAnalysis,
    analyze_equation,
    to_identifier,
)


def test_valid_formula_is_structured_and_function_generated():
    a = analyze_equation(
        expression="C_Rd_c * k * (100 * rho_l * f_ck) ** (1 / 3) * b_w * d",
        symbol="V_Rd_c",
    )
    assert a.valid
    # Free variables become the function parameters, sorted and de-duplicated.
    assert a.variables == ("C_Rd_c", "b_w", "d", "f_ck", "k", "rho_l")
    assert a.function_code.startswith("def V_Rd_c(C_Rd_c, b_w, d, f_ck, k, rho_l):")
    assert "return" in a.function_code
    # The AST is JSON and reflects the top-level multiplication.
    tree = json.loads(a.ast_json)
    assert tree["op"] == "Mult"


def test_allowed_math_function_is_accepted():
    a = analyze_equation(expression="min(1 + sqrt(200 / d), 2.0)", symbol="k")
    assert a.valid
    assert a.variables == ("d",)


def test_import_payload_is_rejected_and_never_executed():
    a = analyze_equation(expression="__import__('os').system('echo hi')", symbol="x")
    assert not a.valid
    assert a.function_code == ""
    assert a.note  # rejected with a reason, never structured, never executed


def test_attribute_access_is_rejected():
    a = analyze_equation(expression="os.system", symbol="x")
    assert not a.valid


def test_arbitrary_call_is_rejected():
    a = analyze_equation(expression="eval('2+2')", symbol="x")
    assert not a.valid
    assert "eval" in a.note


def test_keyword_arguments_are_rejected():
    a = analyze_equation(expression="min(a, b, key=a)", symbol="x")
    assert not a.valid


def test_empty_expression_is_invalid_but_not_an_error():
    a = analyze_equation(expression="", symbol="x")
    assert isinstance(a, EquationAnalysis)
    assert not a.valid
    assert a.note


def test_expression_recovered_from_python_code():
    a = analyze_equation(python_code="def f(a, b):\n    return a + b", symbol="f")
    assert a.valid
    assert a.variables == ("a", "b")


def test_to_identifier_sanitizes_symbols():
    assert to_identifier("V_Rd,c") == "V_Rd_c"
    assert to_identifier("3x") == "_3x"
    assert to_identifier("  ") == ""
