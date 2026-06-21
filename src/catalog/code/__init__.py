"""Code-aware indexing: detect, parse, chunk, and outline source files.

This package makes the catalog treat source code as a first-class artifact
type. It is self-contained and tree-sitter-optional: every entry point degrades
gracefully (empty structure, character chunking) when a grammar is missing, so
importing it never requires the optional ``code`` dependencies.
"""

from __future__ import annotations

from .chunking import chunk_code, select_chunks
from .languages import (
    CODE_EXTENSIONS,
    EXTENSION_LANGUAGE,
    detect_language,
    is_code_path,
)
from .structure import CodeStructure, CodeSymbol, extract_structure
from .to_result import structure_to_result

__all__ = [
    "CODE_EXTENSIONS",
    "EXTENSION_LANGUAGE",
    "detect_language",
    "is_code_path",
    "chunk_code",
    "select_chunks",
    "CodeStructure",
    "CodeSymbol",
    "extract_structure",
    "structure_to_result",
]
