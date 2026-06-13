from __future__ import annotations

from pathlib import Path
from docx import Document


class DocxExtractor:
    def extract_text(self, path: Path) -> str:
        document = Document(path)
        parts = [p.text for p in document.paragraphs if p.text]
        for rel in document.part.rels.values():
            if "hyperlink" in rel.reltype:
                parts.append(rel.target_ref)
        return "\n".join(parts)
