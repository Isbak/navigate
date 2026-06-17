from __future__ import annotations

import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def _pymupdf_pages(path: Path) -> list[str] | None:
    """Per-page text via PyMuPDF, or ``None`` if it is unavailable or errors.

    PyMuPDF preserves reading order far better than ``pypdf`` on multi-column
    and richly laid-out documents, so it is preferred. Returning ``None`` lets
    the caller fall back to ``pypdf`` and keeps tests runnable offline.
    """

    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    try:
        with fitz.open(str(path)) as doc:
            return [page.get_text() or "" for page in doc]
    except Exception:  # noqa: BLE001 - fall back to pypdf
        LOGGER.exception("PyMuPDF extraction failed for %s; falling back to pypdf", path)
        return None


def _pypdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def extract_pdf_pages(path: Path) -> list[str]:
    """Return per-page text for a PDF, preferring PyMuPDF's reading order."""

    pages = _pymupdf_pages(path)
    if pages is None:
        pages = _pypdf_pages(path)
    return pages


class PdfExtractor:
    def extract_text(self, path: Path) -> str:
        return "\n".join(extract_pdf_pages(path))
