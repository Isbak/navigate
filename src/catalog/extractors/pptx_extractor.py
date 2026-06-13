from __future__ import annotations

from pathlib import Path
from pptx import Presentation


class PptxExtractor:
    def extract_text(self, path: Path) -> str:
        prs = Presentation(path)
        parts: list[str] = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text:
                    parts.append(shape.text)
                if getattr(shape, "click_action", None) and shape.click_action.hyperlink.address:
                    parts.append(shape.click_action.hyperlink.address)
        return "\n".join(parts)
