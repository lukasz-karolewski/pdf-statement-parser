from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest
from fpdf import FPDF
from tests.conftest import ExampleBankParser

from pdf_statement_parser import document
from pdf_statement_parser.document import Document, dedupe_chars, layout_chars


def test_document_from_text_pages_and_lines() -> None:
    doc = Document.from_text(["a\nb", "c"], name="synthetic")

    assert doc.page_count == 2
    assert doc.pages[0].number == 1
    assert doc.pages[0].lines == ["a", "b"]
    assert doc.text == "a\nb\fc"
    assert doc.metadata == {}


def test_document_open_pdf_and_fake_parser(example_pdf) -> None:  # type: ignore[no-untyped-def]
    with Document.open(example_pdf) as doc:
        assert doc.page_count == 1
        assert "Example Bank Statement" in doc.text
        assert ExampleBankParser().detect(doc) > 0.9


def _doubled_glyph_pdf(path: Path) -> Path:
    """Bold text drawn twice with a small offset, like Chase headings, plus a rule."""
    pdf = FPDF()
    for page in range(2):
        pdf.add_page()
        pdf.set_font("Helvetica", style="B", size=12)
        for y, text in ((20, "ACCOUNT SUMMARY"), (30, f"Page {page + 1} Previous Balance")):
            pdf.text(20, y, text)
            pdf.text(20.2, y, text)
        pdf.set_font("Helvetica", size=10)
        pdf.set_text_color(200, 0, 0)
        pdf.text(20, 40, "01/05 Coffee -20.00")
        pdf.line(20, 45, 150, 45)
    pdf.output(path)
    return path


_FAST_CHARS_SUPPORTED = tuple(map(int, pdfplumber.__version__.split(".")[:3])) >= (0, 11, 7)


def test_layout_chars_matches_pdfplumber(tmp_path: Path) -> None:
    path = _doubled_glyph_pdf(tmp_path / "doubled.pdf")
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            fast = layout_chars(page)
            if _FAST_CHARS_SUPPORTED:
                assert not hasattr(page, "_objects"), "fast path fell back to page.chars"
            assert fast == page.chars
            assert [list(c) for c in fast] == [list(c) for c in page.chars]


def test_layout_chars_respects_crop_and_filter(tmp_path: Path) -> None:
    path = _doubled_glyph_pdf(tmp_path / "doubled.pdf")
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        cropped = page.crop((0, 0, page.width, 25 * 72 / 25.4))
        filtered = page.filter(lambda obj: obj.get("text") == "A")
        assert layout_chars(cropped) == cropped.chars
        assert layout_chars(filtered) == filtered.chars
        assert 0 < len(cropped.chars) < len(page.chars)


def test_layout_chars_falls_back_when_pdfplumber_disagrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(document, "_fast_chars_enabled", True)
    real = document._fast_layout_chars

    def drifted(page):  # type: ignore[no-untyped-def]
        chars, first = real(page)
        chars[0] = {**chars[0], "stroking_color": None}
        return chars, first

    monkeypatch.setattr(document, "_fast_layout_chars", drifted)
    path = _doubled_glyph_pdf(tmp_path / "doubled.pdf")
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        chars = layout_chars(page)
        assert hasattr(page, "_objects")
        assert chars == page.chars
    assert document._fast_chars_enabled is False


def test_dedupe_chars_matches_pdfplumber(tmp_path: Path) -> None:
    path = _doubled_glyph_pdf(tmp_path / "doubled.pdf")
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            ours = dedupe_chars(page)
            theirs = page.dedupe_chars()
            assert ours.chars == theirs.chars
            assert len(ours.chars) < len(page.chars)
            assert ours.lines == page.lines
            assert ours.extract_text() == theirs.extract_text()


def test_page_text_dedupes_doubled_glyphs(tmp_path: Path) -> None:
    path = _doubled_glyph_pdf(tmp_path / "doubled.pdf")
    with pdfplumber.open(path) as pdf:
        expected = [page.dedupe_chars().extract_text() for page in pdf.pages]
    doc = Document.open(path)
    assert [page.text for page in doc.pages] == expected
    assert doc.pages[0].lines[0] == "ACCOUNT SUMMARY"
    assert doc.pages[0].deduped.chars == doc.pages[0].plumber.dedupe_chars().chars
    doc.close()
    assert all(page._deduped is None for page in doc.pages)
