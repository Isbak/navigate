import fitz  # PyMuPDF

from catalog.extractors import pdf_extractor
from catalog.extractors.pdf_extractor import PdfExtractor, extract_pdf_pages


def _make_pdf(path, pages_text):
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_fast_path_extracts_pymupdf_text(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["Hello from page one", "Second page body"])

    pages = extract_pdf_pages(pdf)
    assert len(pages) == 2
    assert "Hello from page one" in pages[0]
    assert "Second page body" in pages[1]

    text = PdfExtractor().extract_text(pdf)
    assert "Hello from page one" in text
    assert "Second page body" in text


def test_falls_back_to_pypdf_when_pymupdf_unavailable(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["irrelevant"])

    # When PyMuPDF is unavailable/fails, extraction dispatches to pypdf.
    monkeypatch.setattr(pdf_extractor, "_pymupdf_pages", lambda path: None)
    monkeypatch.setattr(pdf_extractor, "_pypdf_pages", lambda path: ["Fallback content"])

    text = PdfExtractor().extract_text(pdf)
    assert text == "Fallback content"
