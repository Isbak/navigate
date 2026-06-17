import fitz  # PyMuPDF

from catalog.extractors import vision_pdf_extractor as vpe
from catalog.extractors.config import VisionConfig
from catalog.extractors.vision_pdf_extractor import VisionPdfExtractor, needs_vision


class FakeVisionProvider:
    """Records each transcription call and returns canned LaTeX."""

    def __init__(self, transcription="$$E = mc^2$$", fail=False):
        self.transcription = transcription
        self.fail = fail
        self.calls = 0
        self.images_seen = 0

    def generate(self, prompt, *, system=None, images=None, image_media_type="image/png"):
        self.calls += 1
        self.images_seen += len(images or [])
        if self.fail:
            raise RuntimeError("vision boom")
        return self.transcription


def _make_pdf(path):
    """Page 1: text-rich (no visuals). Page 2: a figure with little text."""

    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "PAGE ONE TEXT. " * 30)  # well over min_text_chars
    page2 = doc.new_page()
    page2.draw_rect(fitz.Rect(72, 72, 300, 300))  # a vector drawing -> "has visuals"
    doc.save(str(path))
    doc.close()
    return path


def test_needs_vision_triage():
    cfg = VisionConfig(min_text_chars=200)
    # Equation cue flags a text-rich page.
    assert needs_vision("plenty of text " * 50 + " ∑", False, cfg) is True
    # Low text + visuals flags the page.
    assert needs_vision("tiny", True, cfg) is True
    # Plenty of text, no cues, no visuals -> skip.
    assert needs_vision("plenty of text " * 50, False, cfg) is False
    # Low text but no visuals -> skip (likely just a sparse page).
    assert needs_vision("tiny", False, cfg) is False


def test_only_flagged_page_is_transcribed(tmp_path):
    pdf = _make_pdf(tmp_path / "doc.pdf")
    provider = FakeVisionProvider()
    extractor = VisionPdfExtractor(vision=VisionConfig(min_text_chars=200), provider=provider)

    out = extractor.extract_text(pdf)

    # Only the figure page (page 2) was sent to the model.
    assert provider.calls == 1
    assert provider.images_seen == 1
    # Fast text of page 1 is preserved, in order, before the transcription.
    assert "PAGE ONE TEXT." in out
    assert "$$E = mc^2$$" in out
    assert out.index("PAGE ONE TEXT.") < out.index("$$E = mc^2$$")


def test_per_page_failure_falls_back_to_text(tmp_path):
    pdf = _make_pdf(tmp_path / "doc.pdf")
    provider = FakeVisionProvider(fail=True)
    extractor = VisionPdfExtractor(vision=VisionConfig(min_text_chars=200), provider=provider)

    out = extractor.extract_text(pdf)

    assert provider.calls == 1  # it tried
    assert "$$E = mc^2$$" not in out  # but kept the page's fast text
    assert "PAGE ONE TEXT." in out


def test_no_vision_provider_uses_fast_text(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path / "doc.pdf")
    # Provider resolution yields nothing vision-capable.
    monkeypatch.setattr(vpe, "_resolve_claude_provider", lambda: None)
    extractor = VisionPdfExtractor(vision=VisionConfig(min_text_chars=200))

    out = extractor.extract_text(pdf)
    assert "PAGE ONE TEXT." in out


def test_resolve_rejects_non_claude_provider(monkeypatch):
    from catalog.semantic.config import LLMConfig

    monkeypatch.setattr(
        "catalog.semantic.config.load_llm_config",
        lambda *a, **k: LLMConfig(provider="ollama", model="qwen3:14b"),
    )
    assert vpe._resolve_claude_provider() is None
