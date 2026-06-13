from __future__ import annotations

from pathlib import Path
from pypdf import PdfReader


class PdfExtractor:
    def extract_text(self, path: Path) -> str:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
