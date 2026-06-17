from __future__ import annotations

from pathlib import Path

from .config import MODE_FAST, MODE_HIGH_QUALITY
from .docx_extractor import DocxExtractor
from .pdf_extractor import PdfExtractor
from .pptx_extractor import PptxExtractor
from .vision_pdf_extractor import VisionPdfExtractor
from .xlsx_extractor import XlsxExtractor


def get_extractor(path: Path, mode: str = MODE_FAST):
    """Return the extractor for ``path``'s file type, or ``None``.

    In ``high-quality`` mode PDFs use the vision-assisted extractor; every other
    type (and the default ``fast`` mode) uses the cheap text extractors.
    """

    suffix = path.suffix.lower()
    if suffix == ".pdf" and mode == MODE_HIGH_QUALITY:
        return VisionPdfExtractor()
    return {
        ".docx": DocxExtractor(),
        ".pptx": PptxExtractor(),
        ".xlsx": XlsxExtractor(),
        ".pdf": PdfExtractor(),
    }.get(suffix)
