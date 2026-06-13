from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Extractor(Protocol):
    def extract_text(self, path: Path) -> str: ...
