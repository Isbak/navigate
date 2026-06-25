from __future__ import annotations

from pathlib import Path

from .config import MODE_DOCLING, MODE_ENHANCED, MODE_FAST, MODE_HIGH_QUALITY
from .docx_extractor import DocxExtractor
from .pdf_extractor import PdfExtractor
from .pptx_extractor import PptxExtractor
from .vision_pdf_extractor import VisionPdfExtractor
from .xlsx_extractor import XlsxExtractor

_OFFICE_SUFFIXES = {".docx", ".pptx", ".xlsx"}
_ALL_SUPPORTED = _OFFICE_SUFFIXES | {".pdf"}


def get_extractor(path: Path, mode: str = MODE_FAST):
    """Return the extractor for ``path``'s file type, or ``None``.

    Mode routing:

    * ``fast`` (default) — per-format text extractors; no API calls.
    * ``enhanced`` — MarkItDown for office formats (Markdown tables + headings);
      PyMuPDF for PDF. Falls back to fast extractors when MarkItDown is absent.
    * ``docling`` — IBM Docling for all supported formats (PDF + office). Falls
      back to fast extractors when Docling is absent. Must not be used with
      ``workers > 1`` (``DocumentConverter`` is not thread-safe).
    * ``high-quality`` — fast text for office; Claude vision pass for PDF.
    """

    suffix = path.suffix.lower()

    if mode == MODE_DOCLING and suffix in _ALL_SUPPORTED:
        from .docling_extractor import DOCLING_AVAILABLE, DoclingExtractor
        if DOCLING_AVAILABLE:
            return DoclingExtractor()
        # fall through to standard extractors

    if suffix == ".pdf" and mode == MODE_HIGH_QUALITY:
        return VisionPdfExtractor()

    if mode == MODE_ENHANCED and suffix in _OFFICE_SUFFIXES:
        try:
            import markitdown as _markitdown_check  # noqa: F401  # availability probe

            from .markitdown_extractor import MarkItDownExtractor

            return MarkItDownExtractor()
        except ImportError:
            pass  # fall through to standard extractors

    return {
        ".docx": DocxExtractor(),
        ".pptx": PptxExtractor(),
        ".xlsx": XlsxExtractor(),
        ".pdf": PdfExtractor(),
    }.get(suffix)
