"""MarkItDown-backed extractor for office formats (DOCX, PPTX, XLSX).

MarkItDown converts office documents to Markdown, preserving table structure
(``| col |`` rows) and headings (``##``). It does *not* expose embedded
hyperlinks from OPC relationship stores, so the existing format-specific
hyperlink functions are used to supplement.

Install with ``pip install knowledge-catalog[markitdown]``. The import is lazy
so the base package remains unaffected when MarkItDown is absent.
"""

from __future__ import annotations

from pathlib import Path


class MarkItDownExtractor:
    """Wraps MarkItDown for Markdown-structured office document output."""

    _SUPPORTED = {".docx", ".pptx", ".xlsx"}

    def extract_text(self, path: Path) -> str:
        try:
            from markitdown import MarkItDown
        except ImportError as exc:
            raise ImportError(
                "MarkItDown is not installed. "
                "Run: pip install knowledge-catalog[markitdown]"
            ) from exc

        mid = MarkItDown()
        result = mid.convert(str(path))
        text = result.text_content or ""

        hyperlinks = self._embedded_hyperlinks(path)
        if hyperlinks:
            text = text + "\n" + "\n".join(hyperlinks)
        return text

    def _embedded_hyperlinks(self, path: Path) -> list[str]:
        """Return embedded hyperlink targets not surfaced by MarkItDown."""
        suffix = path.suffix.lower()
        if suffix == ".docx":
            from .docx_extractor import extract_docx_hyperlinks
            return extract_docx_hyperlinks(path)
        if suffix == ".pptx":
            from .pptx_extractor import extract_pptx_hyperlinks
            return extract_pptx_hyperlinks(path)
        if suffix == ".xlsx":
            from .xlsx_extractor import extract_xlsx_hyperlinks
            return extract_xlsx_hyperlinks(path)
        return []
