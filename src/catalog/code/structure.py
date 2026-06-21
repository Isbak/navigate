"""Deterministic code structure extraction via tree-sitter.

``extract_structure`` parses a source file and reads off its imports, top-level
functions, and classes (with their methods) - line spans, signatures, and a
``public`` flag - **without executing anything**. It is the code analogue of
reading a document's headings: a reliable, LLM-free index that the semantic
layer later enriches with purpose, risks, and cross-symbol relationships.

The walk uses a small set of tree-sitter node-type names that are largely
consistent across grammars (``function_definition``, ``class_declaration``,
``import_statement``, ...), so one generic recursion covers many languages.
When no grammar is available the result is an empty structure and the caller
falls back to plain-text handling.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .parser import get_parser

# Node-type names that denote a callable definition, a class-like type, and an
# import, respectively. These names are shared across most tree-sitter grammars;
# unknown grammars simply contribute fewer matches rather than wrong ones.
_FUNCTION_TYPES = frozenset(
    {
        "function_definition",
        "function_declaration",
        "function_item",
        "method_definition",
        "method_declaration",
        "method",
        "constructor_declaration",
    }
)
_CLASS_TYPES = frozenset(
    {
        "class_definition",
        "class_declaration",
        "class_specifier",
        "interface_declaration",
        "struct_item",
        "struct_specifier",
        "trait_item",
        "enum_declaration",
        "enum_specifier",
        "impl_item",
        "object_declaration",
    }
)
_IMPORT_TYPES = frozenset(
    {
        "import_statement",
        "import_from_statement",
        "import_declaration",
        "use_declaration",
        "preproc_include",
        "package_clause",
    }
)
# Child node types that carry a definition's name when there is no ``name`` field.
_NAME_TYPES = frozenset(
    {"identifier", "type_identifier", "field_identifier", "name", "constant", "word"}
)

_MAX_SIGNATURE = 200


@dataclass(frozen=True)
class CodeSymbol:
    """One named construct (function, method, or class) in a source file."""

    kind: str  # "function" | "method" | "class"
    name: str
    start_line: int  # 1-based
    end_line: int  # 1-based
    signature: str = ""
    public: bool = True
    parent: str = ""  # enclosing class name, for methods


@dataclass(frozen=True)
class CodeStructure:
    """The parsed outline of a single source file."""

    language: str
    imports: tuple[str, ...] = ()
    classes: tuple[CodeSymbol, ...] = ()
    functions: tuple[CodeSymbol, ...] = ()
    methods: tuple[CodeSymbol, ...] = ()
    parsed: bool = False  # False when no grammar was available

    def is_empty(self) -> bool:
        return not (self.imports or self.classes or self.functions or self.methods)

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "parsed": self.parsed,
            "imports": list(self.imports),
            "classes": [asdict(c) for c in self.classes],
            "functions": [asdict(f) for f in self.functions],
            "methods": [asdict(m) for m in self.methods],
        }


def _node_text(node) -> str:
    raw = getattr(node, "text", None)
    if raw is None:
        return ""
    return raw.decode("utf-8", "replace")


def _name_of(node) -> str:
    field_name = node.child_by_field_name("name")
    if field_name is not None:
        return _node_text(field_name).strip()
    for child in node.children:
        if child.type in _NAME_TYPES:
            return _node_text(child).strip()
    return ""


def _signature_of(node) -> str:
    first_line = _node_text(node).splitlines()[:1]
    text = first_line[0].strip() if first_line else ""
    return text[:_MAX_SIGNATURE]


def _is_public(name: str, language: str) -> bool:
    if not name:
        return False
    if language == "python":
        return not name.startswith("_")
    if language == "go":
        return name[0].isupper()
    return True


@dataclass
class _Accumulator:
    language: str
    imports: list[str] = field(default_factory=list)
    classes: list[CodeSymbol] = field(default_factory=list)
    functions: list[CodeSymbol] = field(default_factory=list)
    methods: list[CodeSymbol] = field(default_factory=list)


def _visit(node, enclosing_class: str, acc: _Accumulator) -> None:
    """Recursively collect imports and definitions under ``node``.

    Function bodies are not recursed into (nested helpers are intentionally
    skipped to keep the outline at module/class granularity); class bodies are,
    so their methods are attributed to the class.
    """

    for child in node.children:
        ntype = child.type
        if ntype in _IMPORT_TYPES:
            text = _node_text(child).strip().splitlines()[:1]
            if text:
                acc.imports.append(text[0][:_MAX_SIGNATURE])
            continue
        if ntype in _CLASS_TYPES:
            name = _name_of(child)
            acc.classes.append(
                CodeSymbol(
                    kind="class",
                    name=name,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    signature=_signature_of(child),
                    public=_is_public(name, acc.language),
                    parent=enclosing_class,
                )
            )
            body = child.child_by_field_name("body") or child
            _visit(body, name, acc)
            continue
        if ntype in _FUNCTION_TYPES:
            name = _name_of(child)
            symbol = CodeSymbol(
                kind="method" if enclosing_class else "function",
                name=name,
                start_line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
                signature=_signature_of(child),
                public=_is_public(name, acc.language),
                parent=enclosing_class,
            )
            (acc.methods if enclosing_class else acc.functions).append(symbol)
            continue
        # Wrapper nodes (decorators, exports, namespaces, top-level blocks) are
        # transparent: recurse so the definitions they contain are still found.
        _visit(child, enclosing_class, acc)


def extract_structure(code: str, language: str | None) -> CodeStructure:
    """Parse ``code`` and return its :class:`CodeStructure`.

    Returns an unparsed, empty structure when ``language`` is unknown or no
    tree-sitter grammar is installed - never raises.
    """

    if not language:
        return CodeStructure(language="")
    parser = get_parser(language)
    if parser is None:
        return CodeStructure(language=language)
    try:
        tree = parser.parse(code.encode("utf-8"))
        root = tree.root_node
    except Exception:  # noqa: BLE001 - malformed input must not abort indexing
        return CodeStructure(language=language)

    acc = _Accumulator(language=language)
    _visit(root, "", acc)
    return CodeStructure(
        language=language,
        imports=tuple(acc.imports),
        classes=tuple(acc.classes),
        functions=tuple(acc.functions),
        methods=tuple(acc.methods),
        parsed=True,
    )


__all__ = ["CodeSymbol", "CodeStructure", "extract_structure"]
