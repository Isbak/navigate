"""Safe, non-executing validation and structuring of extracted equations.

An extracted equation is *untrusted*: it comes from an LLM or a curated catalog
and is only trusted once a human approves it, exactly like every other candidate
on the platform. This module is the safety boundary for the equation's
machine-readable payload. **It never executes the equation.** It parses the
formula with :mod:`ast` (a syntax tree, not an interpreter), checks every node
against a strict allowlist, and projects the expression into a JSON AST plus the
set of variables it reads - so a reviewer (and a future evaluator) gets an
auditable structure rather than a free-text formula or arbitrary code.

The allowlist is deliberately narrow: arithmetic, comparisons, conditional
expressions, a handful of named math functions, numeric/boolean constants, and
variable reads. Everything else - imports, attribute access, arbitrary or
keyword calls, comprehensions, lambdas, subscripts, assignments - is rejected,
because none of it belongs in a design formula and all of it is how untrusted
code does harm.

The canonical representation produced here is a Python *function*
(``def <symbol>(<vars>): return <expression>``) whose parameters are exactly the
free variables of the expression, so it is well-formed by construction and never
needs to be executed to be understood.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field

# Named functions an equation may call. Pure, side-effect-free math only; these
# map one-to-one onto ``math`` / builtins for a future sandboxed evaluator, but
# nothing here imports or calls them - they are merely permitted in the tree.
ALLOWED_FUNCTIONS = frozenset(
    {
        "abs", "min", "max", "round", "pow", "sum",
        "sqrt", "exp", "log", "log10", "log2",
        "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
        "sinh", "cosh", "tanh", "hypot", "floor", "ceil",
        "degrees", "radians",
    }
)

# Bare names that are constants rather than variables (so they are not treated as
# free variables / function parameters).
ALLOWED_CONSTANTS = frozenset({"pi", "e", "tau", "inf"})

# AST node types permitted anywhere in an expression. Anything not listed is a
# rejection - the allowlist is the whole point.
_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    # operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
)

_IDENT_RE = re.compile(r"[^0-9a-zA-Z_]+")


class EquationError(ValueError):
    """Raised when an equation expression contains a disallowed construct."""


@dataclass(frozen=True)
class EquationAnalysis:
    """The validated, structured form of one extracted equation.

    ``valid`` is False for an empty, unparseable, or disallowed expression; the
    equation is still *kept* (with ``note`` explaining why) so a human sees it at
    review rather than it being silently dropped.
    """

    valid: bool
    expression: str = ""
    function_code: str = ""
    ast_json: str = ""
    variables: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""


def to_identifier(value: str) -> str:
    """Coerce a math symbol (e.g. ``V_Rd,c``) into a valid Python identifier."""

    slug = _IDENT_RE.sub("_", (value or "").strip()).strip("_")
    if not slug:
        return ""
    if slug[0].isdigit():
        slug = f"_{slug}"
    return slug


def _ensure_allowed(tree: ast.AST) -> None:
    """Walk the tree and reject any node outside the allowlist."""

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise EquationError("only direct calls to named math functions are allowed")
            if node.func.id not in ALLOWED_FUNCTIONS:
                raise EquationError(f"function '{node.func.id}' is not on the allowlist")
            if node.keywords:
                raise EquationError("keyword arguments are not allowed in equations")
            continue
        if not isinstance(node, _ALLOWED_NODES):
            raise EquationError(
                f"disallowed expression construct: {type(node).__name__}"
            )


def _free_variables(tree: ast.AST) -> tuple[str, ...]:
    """Sorted, de-duplicated variable names the expression reads.

    A name is a variable unless it is a called function or a permitted constant.
    """

    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    names = {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name)
        and n.id not in called
        and n.id not in ALLOWED_CONSTANTS
        and n.id not in ALLOWED_FUNCTIONS
    }
    return tuple(sorted(names))


def _node_to_dict(node: ast.AST) -> object:
    """Project an allowed expression node into a compact JSON-able structure."""

    if isinstance(node, ast.Expression):
        return _node_to_dict(node.body)
    if isinstance(node, ast.Constant):
        return {"const": node.value}
    if isinstance(node, ast.Name):
        return {"var": node.id}
    if isinstance(node, ast.BinOp):
        return {
            "op": type(node.op).__name__,
            "left": _node_to_dict(node.left),
            "right": _node_to_dict(node.right),
        }
    if isinstance(node, ast.UnaryOp):
        return {"unary": type(node.op).__name__, "operand": _node_to_dict(node.operand)}
    if isinstance(node, ast.BoolOp):
        return {"bool": type(node.op).__name__, "values": [_node_to_dict(v) for v in node.values]}
    if isinstance(node, ast.Compare):
        return {
            "compare": _node_to_dict(node.left),
            "ops": [type(o).__name__ for o in node.ops],
            "comparators": [_node_to_dict(c) for c in node.comparators],
        }
    if isinstance(node, ast.IfExp):
        return {
            "if": _node_to_dict(node.test),
            "then": _node_to_dict(node.body),
            "else": _node_to_dict(node.orelse),
        }
    if isinstance(node, ast.Call):
        return {
            "call": node.func.id,  # type: ignore[union-attr]
            "args": [_node_to_dict(a) for a in node.args],
        }
    # Unreachable once _ensure_allowed has passed, but kept defensive.
    return {"unknown": type(node).__name__}


def _expression_from_function(code: str) -> str:
    """Best-effort extraction of the returned expression from a function body."""

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            try:
                return ast.unparse(node.value)
            except Exception:  # noqa: BLE001 - unparse is best-effort
                return ""
    return ""


def _render_function(name: str, variables: tuple[str, ...], expression: str) -> str:
    """Render the canonical ``def name(vars): return expression`` form."""

    params = ", ".join(variables)
    return f"def {name}({params}):\n    return {expression}"


def analyze_equation(
    *,
    expression: str = "",
    symbol: str = "",
    python_code: str = "",
) -> EquationAnalysis:
    """Validate and structure one equation without ever executing it.

    Prefers ``expression`` (a single Python-syntax formula). When only
    ``python_code`` is supplied, the returned expression is recovered from its
    ``return`` statement. The result carries the canonical function form, a JSON
    AST, and the variables read; an invalid equation returns ``valid=False`` with
    a human-readable ``note`` and is never dropped by the caller.
    """

    expr = (expression or "").strip()
    if not expr and python_code.strip():
        expr = _expression_from_function(python_code).strip()
    if not expr:
        return EquationAnalysis(valid=False, note="no expression to analyze")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return EquationAnalysis(
            valid=False, expression=expr, note=f"could not parse expression: {exc.msg}"
        )

    try:
        _ensure_allowed(tree)
    except EquationError as exc:
        return EquationAnalysis(valid=False, expression=expr, note=str(exc))

    variables = _free_variables(tree)
    func_name = to_identifier(symbol) or "evaluate"
    function_code = _render_function(func_name, variables, expr)
    ast_json = json.dumps(_node_to_dict(tree), separators=(",", ":"))
    return EquationAnalysis(
        valid=True,
        expression=expr,
        function_code=function_code,
        ast_json=ast_json,
        variables=variables,
    )


__all__ = [
    "ALLOWED_FUNCTIONS",
    "ALLOWED_CONSTANTS",
    "EquationError",
    "EquationAnalysis",
    "analyze_equation",
    "to_identifier",
]
