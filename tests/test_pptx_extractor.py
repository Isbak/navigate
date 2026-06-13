"""Regression tests for the PowerPoint text/hyperlink extractor."""

from __future__ import annotations

from pathlib import Path

from catalog.extractors import pptx_extractor
from catalog.extractors.pptx_extractor import PptxExtractor


class _UnsupportedClickActionShape:
    @property
    def click_action(self):  # noqa: ANN201 - mirrors python-pptx dynamic API
        raise TypeError("a group shape cannot have a click action")


class _LinkedShape:
    text = "Open spec"

    class click_action:  # noqa: N801 - mirrors python-pptx attribute shape
        class hyperlink:  # noqa: N801 - mirrors python-pptx attribute shape
            address = "https://example.com/spec"


class _GroupShape(_UnsupportedClickActionShape):
    text = ""
    shapes = [_LinkedShape()]


class _Shapes(list):
    pass


class _Slide:
    shapes = _Shapes([_GroupShape()])


class _Presentation:
    slides = [_Slide()]


def test_extract_text_skips_group_click_action_and_reads_child_shapes(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pptx_extractor, "Presentation", lambda path: _Presentation())
    path = tmp_path / "deck.pptx"

    text = PptxExtractor().extract_text(path)

    assert "Open spec" in text
    assert "https://example.com/spec" in text
