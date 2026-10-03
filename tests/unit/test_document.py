from __future__ import annotations

from tests.conftest import ExampleBankParser

from pdf_statement_parser.document import Document


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
