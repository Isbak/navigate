"""Vision-assisted PDF extraction for the ``high-quality`` mode.

The fast text pass runs first; only the pages it cannot read well - scanned
pages, figures, and equations rendered as images - are rendered to an image and
transcribed by a Claude vision model into Markdown with LaTeX equations. Pages
the fast pass reads fine keep their text verbatim, so model cost stays
proportional to the suspect pages rather than the whole document.

Everything degrades gracefully to fast text: missing PyMuPDF, no vision-capable
provider, a per-page render error, or a model failure all fall back to the
page's fast-extracted text so a cache entry is always produced.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import VisionConfig, load_extraction_config
from .pdf_extractor import extract_pdf_pages

LOGGER = logging.getLogger(__name__)

VISION_SYSTEM = (
    "You transcribe a single page of a document image into clean Markdown. "
    "Reproduce all text faithfully in reading order. Render every mathematical "
    "equation as LaTeX delimited by $...$ (inline) or $$...$$ (display), and "
    "tables as Markdown tables. Do not summarize, explain, or add anything that "
    "is not on the page. Output only the transcription."
)
VISION_PROMPT = "Transcribe this page to Markdown, with equations as LaTeX."


def needs_vision(text: str, has_visuals: bool, vision: VisionConfig) -> bool:
    """Decide whether a page should be sent to the vision model.

    A page is flagged when its fast text shows equation cues, or when it has
    little extractable text but does carry images/vector drawings (a likely
    scanned page or image-rendered formula).
    """

    if any(ch in text for ch in vision.equation_cues):
        return True
    return has_visuals and len(text.strip()) < vision.min_text_chars


def _resolve_claude_provider():
    """Build the configured provider, or ``None`` if it cannot do vision."""

    try:
        from catalog.semantic.config import load_llm_config
        from catalog.semantic.providers import build_provider
        from catalog.semantic.providers.claude_provider import ClaudeProvider

        provider = build_provider(load_llm_config())
    except Exception:  # noqa: BLE001 - any config/import problem -> text only
        LOGGER.exception("Could not build a vision provider; using fast text only")
        return None
    if not isinstance(provider, ClaudeProvider):
        LOGGER.info(
            "Configured provider %r is not vision-capable; using fast text only",
            type(provider).__name__,
        )
        return None
    return provider


class VisionPdfExtractor:
    """PDF extractor that transcribes only the pages the fast pass can't read."""

    def __init__(self, vision: VisionConfig | None = None, provider=None) -> None:
        self._vision = vision or load_extraction_config().vision
        # ``provider`` is injectable for tests; when omitted it is resolved from
        # config on first use (and may be ``None`` if vision is unavailable).
        self._provider = provider
        self._provider_resolved = provider is not None

    def _get_provider(self):
        if not self._provider_resolved:
            self._provider = _resolve_claude_provider()
            self._provider_resolved = True
        return self._provider

    def extract_text(self, path: Path) -> str:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            LOGGER.warning("PyMuPDF unavailable; vision mode falls back to fast text")
            return "\n".join(extract_pdf_pages(path))

        provider = self._get_provider()
        if provider is None:
            return "\n".join(extract_pdf_pages(path))

        pages_out: list[str] = []
        rendered = 0
        with fitz.open(str(path)) as doc:
            for page in doc:
                text = page.get_text() or ""
                has_visuals = bool(page.get_images()) or bool(page.get_drawings())
                if (
                    rendered < self._vision.max_pages
                    and needs_vision(text, has_visuals, self._vision)
                ):
                    transcription = self._transcribe(page, provider)
                    if transcription:
                        rendered += 1
                        pages_out.append(transcription)
                        continue
                pages_out.append(text)
        return "\n".join(pages_out)

    def _transcribe(self, page, provider) -> str | None:
        try:
            png = page.get_pixmap(dpi=self._vision.dpi).tobytes("png")
            return provider.generate(VISION_PROMPT, system=VISION_SYSTEM, images=[png])
        except Exception:  # noqa: BLE001 - keep the page's fast text on any failure
            LOGGER.exception("Vision transcription failed for a page; keeping text")
            return None


__all__ = ["VisionPdfExtractor", "needs_vision", "VISION_SYSTEM", "VISION_PROMPT"]
