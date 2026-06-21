"""Project a :class:`CodeStructure` onto the shared classification model.

The deterministic outline produced by tree-sitter is mapped onto the very same
``ClassificationResult`` the LLM produces, so it flows through the existing
parse -> persist -> consolidate -> graph pipeline with no new storage. A file
becomes a ``Module`` entity; its classes, functions, and methods become
entities linked by ``defines``; its imports become ``Library`` entities linked
by ``imports``.

These observations are read directly off the syntax tree, so they carry a high
confidence. ``type_confidence`` is deliberately ``0.0`` so that when this result
is merged with the LLM's, the model's document-level summary and type win while
the precise structural entities/relationships are folded in (and beat any
lower-confidence duplicates the model proposed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .structure import CodeStructure

if TYPE_CHECKING:
    from ..semantic.models import ClassificationResult

# Tree-sitter reads these straight off the source, so they are near-certain.
_STRUCTURE_CONFIDENCE = 0.99


def _module_name(metadata: dict) -> str:
    return str(
        metadata.get("filename")
        or metadata.get("path")
        or metadata.get("artifact_id")
        or "module"
    )


def _import_name(statement: str) -> str:
    """Best-effort extraction of the imported module/package from a statement."""

    text = statement.strip()
    # Quoted target (JS/TS ``from "x"``, Python rare): take the first quoted span.
    for quote in ('"', "'"):
        if quote in text:
            start = text.index(quote)
            end = text.find(quote, start + 1)
            if end > start:
                return text[start + 1 : end]
    tokens = text.replace(",", " ").split()
    if not tokens:
        return ""
    head = tokens[0]
    if head in {"from", "import", "use", "package"} and len(tokens) > 1:
        return tokens[1].rstrip(";")
    if head == "#include" and len(tokens) > 1:
        return tokens[1].strip("<>\"")
    return head.rstrip(";")


def structure_to_result(
    structure: CodeStructure, metadata: dict
) -> ClassificationResult:
    """Build a structural ``ClassificationResult`` from a parsed code outline."""

    # Imported here rather than at module load: the semantic package imports this
    # `code` package, so a top-level import would create a startup cycle.
    from ..semantic.models import (
        CandidateEntity,
        CandidateRelationship,
        ClassificationResult,
    )

    module = _module_name(metadata)
    entities: list[CandidateEntity] = []
    relationships: list[CandidateRelationship] = []

    if structure.is_empty():
        # Still record the module itself so the file is represented as code.
        entities.append(CandidateEntity("Module", module, _STRUCTURE_CONFIDENCE))
        return ClassificationResult(
            document_type="Source Code",
            type_confidence=0.0,
            entities=entities,
        )

    entities.append(CandidateEntity("Module", module, _STRUCTURE_CONFIDENCE))

    seen_libs: set[str] = set()
    for statement in structure.imports:
        lib = _import_name(statement)
        if not lib or lib in seen_libs:
            continue
        seen_libs.add(lib)
        entities.append(CandidateEntity("Library", lib, _STRUCTURE_CONFIDENCE))
        relationships.append(
            CandidateRelationship(
                module, "imports", lib, _STRUCTURE_CONFIDENCE, statement
            )
        )

    for cls in structure.classes:
        if not cls.name:
            continue
        entities.append(
            CandidateEntity("Class", cls.name, _STRUCTURE_CONFIDENCE, cls.signature)
        )
        relationships.append(
            CandidateRelationship(module, "defines", cls.name, _STRUCTURE_CONFIDENCE)
        )

    for fn in structure.functions:
        if not fn.name:
            continue
        entities.append(
            CandidateEntity("Function", fn.name, _STRUCTURE_CONFIDENCE, fn.signature)
        )
        relationships.append(
            CandidateRelationship(module, "defines", fn.name, _STRUCTURE_CONFIDENCE)
        )

    for method in structure.methods:
        if not method.name:
            continue
        qualified = f"{method.parent}.{method.name}" if method.parent else method.name
        entities.append(
            CandidateEntity(
                "Function", qualified, _STRUCTURE_CONFIDENCE, method.signature
            )
        )
        if method.parent:
            relationships.append(
                CandidateRelationship(
                    method.parent, "defines", qualified, _STRUCTURE_CONFIDENCE
                )
            )

    return ClassificationResult(
        document_type="Source Code",
        type_confidence=0.0,
        entities=entities,
        relationships=relationships,
    )


__all__ = ["structure_to_result"]
