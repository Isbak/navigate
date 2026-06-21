"""Lazy, fault-tolerant tree-sitter parser loading.

Tree-sitter and its per-language grammars are *optional* dependencies (the
``code`` extra). This module loads a parser for a given language on demand and
**never raises** when tree-sitter or a grammar is missing: it returns ``None``
and the caller falls back to language-agnostic behaviour (character chunking,
empty structure). This mirrors the defensive, no-execution stance of
:mod:`catalog.semantic.equation_ast`.

Each grammar lives in its own wheel (``tree-sitter-python`` etc.) that exposes a
module-level function returning a grammar pointer. The map below records the
module and the function name (a few grammars, like TypeScript and PHP, ship more
than one grammar per wheel).
"""

from __future__ import annotations

import importlib
import logging
from functools import cache

LOGGER = logging.getLogger(__name__)

# language -> (import module, grammar-function name). The function returns the
# PyCapsule that ``tree_sitter.Language`` wraps.
_GRAMMARS: dict[str, tuple[str, str]] = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "java": ("tree_sitter_java", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "c_sharp": ("tree_sitter_c_sharp", "language"),
    "ruby": ("tree_sitter_ruby", "language"),
    "php": ("tree_sitter_php", "language_php"),
    "bash": ("tree_sitter_bash", "language"),
}


@cache
def _load_language(language: str):
    """Return a ``tree_sitter.Language`` for ``language`` or ``None``.

    Cached because building a ``Language`` is the expensive, shareable step; the
    cache also means a missing grammar is only probed once.
    """

    spec = _GRAMMARS.get(language)
    if spec is None:
        return None
    try:
        import tree_sitter
    except Exception:  # noqa: BLE001 - tree-sitter is an optional dependency
        LOGGER.debug("tree-sitter is not installed; code parsing disabled")
        return None

    module_name, func_name = spec
    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - grammar wheel not installed
        LOGGER.debug("grammar %s for %s is not installed", module_name, language)
        return None

    getter = getattr(module, func_name, None) or getattr(module, "language", None)
    if getter is None:
        return None
    try:
        return tree_sitter.Language(getter())
    except Exception:  # noqa: BLE001 - incompatible grammar/runtime versions
        LOGGER.debug("failed to build tree-sitter language for %s", language, exc_info=True)
        return None


def get_parser(language: str | None):
    """Return a fresh ``tree_sitter.Parser`` for ``language``, or ``None``.

    A new ``Parser`` is returned per call (parsers are not thread-safe) while the
    underlying ``Language`` is cached. ``None`` means tree-sitter or the grammar
    is unavailable and the caller should degrade gracefully.
    """

    if not language:
        return None
    lang = _load_language(language)
    if lang is None:
        return None
    try:
        import tree_sitter

        return tree_sitter.Parser(lang)
    except Exception:  # noqa: BLE001 - never let parsing setup crash the catalog
        return None


def supported_languages() -> frozenset[str]:
    """The languages this module knows how to load a grammar for."""

    return frozenset(_GRAMMARS)


__all__ = ["get_parser", "supported_languages"]
