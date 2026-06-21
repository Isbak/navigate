"""Source-code language detection by file extension.

The catalog ingests source code alongside documents. This module is the single
place that decides whether a path is code and, if so, which tree-sitter grammar
to use for it. Keeping the mapping here lets the scanner, extraction, and
classification layers agree on one definition of "code" without importing
tree-sitter (which is optional).
"""

from __future__ import annotations

from pathlib import Path

# Map a lowercase file extension (including the dot) to a tree-sitter language
# name. The language name is also the key used by :mod:`catalog.code.parser` to
# load the matching grammar. Several extensions can share one language.
EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "bash",
    ".bash": "bash",
}

# Every extension the scanner treats as source code. Frozen so callers can union
# it with ``SUPPORTED_EXTENSIONS`` without mutating it.
CODE_EXTENSIONS: frozenset[str] = frozenset(EXTENSION_LANGUAGE)


def detect_language(path: str | Path) -> str | None:
    """Return the tree-sitter language for ``path``, or ``None`` if not code."""

    return EXTENSION_LANGUAGE.get(Path(path).suffix.lower())


def is_code_path(path: str | Path) -> bool:
    """True when ``path`` has a recognized source-code extension."""

    return Path(path).suffix.lower() in CODE_EXTENSIONS


__all__ = [
    "EXTENSION_LANGUAGE",
    "CODE_EXTENSIONS",
    "detect_language",
    "is_code_path",
]
