from __future__ import annotations

from pathlib import Path

from .docx_extractor import DocxExtractor
from .pdf_extractor import PdfExtractor
from .pptx_extractor import PptxExtractor
from .xlsx_extractor import XlsxExtractor


def get_extractor(path: Path):
    return {
        ".docx": DocxExtractor(),
        ".pptx": PptxExtractor(),
        ".xlsx": XlsxExtractor(),
        ".pdf": PdfExtractor(),
    }.get(path.suffix.lower())
