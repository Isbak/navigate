"""Docling-backed extractor for PDF, DOCX, PPTX, and XLSX.

IBM Docling uses a DocLayNet layout model to extract text with correct reading
order, Markdown table structure, and per-element provenance (page number,
bounding box). The ``DocumentConverter`` is a module-level singleton to avoid
the 5–15 s model-load overhead on every document.

Install with ``pip install knowledge-catalog[docling]``. The converter is only
instantiated when this module is first imported, so the base package is
unaffected when Docling is absent.

Thread-safety note: ``DocumentConverter.convert()`` is not guaranteed
thread-safe. ``extract_all()`` automatically sets ``workers=1`` when
``mode=docling`` to avoid concurrent access.
"""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_CONVERTER = None
_IMPORT_ERROR: ImportError | None = None
DOCLING_AVAILABLE: bool = False

try:
    from docling.document_converter import DocumentConverter

    _CONVERTER = DocumentConverter()
    DOCLING_AVAILABLE = True
except ImportError as _exc:
    _IMPORT_ERROR = _exc


def _require_converter():
    if _CONVERTER is None:
        raise ImportError(
            "Docling is not installed. "
            "Run: pip install knowledge-catalog[docling]"
        ) from _IMPORT_ERROR
    return _CONVERTER


class DoclingExtractor:
    """Wraps Docling for high-quality PDF and office document extraction."""

    def extract_text(self, path: Path) -> str:
        converter = _require_converter()
        result = converter.convert(str(path))
        return result.document.export_to_markdown()

    def extract_with_lineage(self, path: Path) -> tuple[str, list[dict]]:
        """Return ``(markdown_text, per_element_lineage)``.

        Each lineage entry maps ``page`` (1-based int or None), ``type``
        (Docling item class name), and ``text`` (the element's text content).
        The caller writes this as ``lineage.json`` alongside ``extracted.txt``.
        """
        converter = _require_converter()
        result = converter.convert(str(path))
        doc = result.document

        lineage: list[dict] = []
        for item, _level in doc.iterate_items():
            prov = getattr(item, "prov", None)
            page = prov[0].page_no if prov else None
            lineage.append(
                {
                    "page": page,
                    "type": type(item).__name__,
                    "text": getattr(item, "text", "") or "",
                }
            )

        return doc.export_to_markdown(), lineage
