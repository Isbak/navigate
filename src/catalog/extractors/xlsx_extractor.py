from __future__ import annotations

from pathlib import Path
from openpyxl import load_workbook


class XlsxExtractor:
    def extract_text(self, path: Path) -> str:
        wb = load_workbook(path, read_only=True, data_only=True)
        parts: list[str] = []
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        if cell.value is not None:
                            parts.append(str(cell.value))
                        if cell.hyperlink and cell.hyperlink.target:
                            parts.append(cell.hyperlink.target)
        finally:
            wb.close()
        return "\n".join(parts)
