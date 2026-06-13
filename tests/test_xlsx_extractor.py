"""Regression tests for the xlsx text/hyperlink extractor.

The extractor opens workbooks with ``read_only=True``, where openpyxl yields
``ReadOnlyCell``/``EmptyCell`` objects that do not define a ``hyperlink``
attribute. Touching ``cell.hyperlink`` directly therefore raised
``AttributeError`` and aborted extraction, so these tests pin the guarded
behaviour against a real on-disk workbook.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catalog.extractors.xlsx_extractor import XlsxExtractor

openpyxl = pytest.importorskip("openpyxl")


def _write_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Cost Model"
    ws["B1"] = 42
    linked = ws["A2"]
    linked.value = "Spec"
    linked.hyperlink = "https://example.com/spec"
    # Leave C-column cells empty so iter_rows() yields EmptyCell objects too.
    wb.save(path)
    wb.close()


def test_extract_text_includes_values_and_hyperlinks(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    _write_workbook(path)

    text = XlsxExtractor().extract_text(path)

    assert "Cost Model" in text
    assert "42" in text
    assert "Spec" in text
    assert "https://example.com/spec" in text


def test_extract_text_handles_empty_cells_without_error(tmp_path: Path) -> None:
    path = tmp_path / "sparse.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    # A value far from the origin guarantees a block of empty cells before it,
    # which previously triggered AttributeError on EmptyCell.hyperlink.
    ws["D5"] = "Lookup"
    wb.save(path)
    wb.close()

    # Should not raise; the lone value is still captured.
    assert XlsxExtractor().extract_text(path) == "Lookup"
