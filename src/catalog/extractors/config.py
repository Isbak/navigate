"""Configuration for the extraction layer.

Reads ``config/extraction.yml`` and exposes a small :class:`ExtractionConfig`.
The loader is tolerant: a missing file (or a missing key) falls back to safe
``fast`` defaults so extraction works out of the box and fully offline.

``mode`` selects between the cheap text-only path and the opt-in vision path:

    fast          text-only extraction, no API calls.
    high-quality  PDFs additionally get a selective vision pass (see
                  :mod:`catalog.extractors.vision_pdf_extractor`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_EXTRACTION_CONFIG_PATH = Path("config/extraction.yml")

MODE_FAST = "fast"
MODE_HIGH_QUALITY = "high-quality"
VALID_MODES = (MODE_FAST, MODE_HIGH_QUALITY)

DEFAULT_DPI = 200
DEFAULT_MAX_PAGES = 50
DEFAULT_MIN_TEXT_CHARS = 200
DEFAULT_EQUATION_CUES = "∑∫∏√≤≥±×÷αβγδθλμπσφω"


@dataclass(frozen=True)
class VisionConfig:
    """Settings for the selective vision pass over PDF pages."""

    dpi: int = DEFAULT_DPI
    max_pages: int = DEFAULT_MAX_PAGES
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS
    equation_cues: str = DEFAULT_EQUATION_CUES


@dataclass(frozen=True)
class ExtractionConfig:
    """Resolved extraction configuration."""

    mode: str = MODE_FAST
    vision: VisionConfig = field(default_factory=VisionConfig)

    @property
    def high_quality(self) -> bool:
        return self.mode == MODE_HIGH_QUALITY


def _normalize_mode(value: object) -> str:
    mode = str(value or MODE_FAST).strip().lower()
    return mode if mode in VALID_MODES else MODE_FAST


def load_extraction_config(
    path: str | Path = DEFAULT_EXTRACTION_CONFIG_PATH,
) -> ExtractionConfig:
    config_path = Path(path)
    if not config_path.exists():
        return ExtractionConfig()

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    mode = _normalize_mode(raw.get("mode"))
    block = dict(raw.get("vision", {}) or {})
    vision = VisionConfig(
        dpi=int(block.get("dpi", DEFAULT_DPI)),
        max_pages=int(block.get("max_pages", DEFAULT_MAX_PAGES)),
        min_text_chars=int(block.get("min_text_chars", DEFAULT_MIN_TEXT_CHARS)),
        equation_cues=str(block.get("equation_cues", DEFAULT_EQUATION_CUES)),
    )
    return ExtractionConfig(mode=mode, vision=vision)


__all__ = [
    "ExtractionConfig",
    "VisionConfig",
    "load_extraction_config",
    "DEFAULT_EXTRACTION_CONFIG_PATH",
    "MODE_FAST",
    "MODE_HIGH_QUALITY",
    "VALID_MODES",
]
