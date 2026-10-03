from __future__ import annotations

import pytest
from tests.conftest import ExampleBankParser

from pdf_statement_parser.api import detect, parse
from pdf_statement_parser.document import Document
from pdf_statement_parser.exceptions import UnknownParserError, UnsupportedStatementError
from pdf_statement_parser.registry import ParserRegistry


def test_detect_and_parse_with_fresh_registry(example_text: str) -> None:
    registry = ParserRegistry([ExampleBankParser()])
    doc = Document.from_text(example_text, name="example.txt")

    detection = detect(doc, registry=registry)
    result = parse(doc, registry=registry)

    assert detection is not None
    assert detection.parser_id == "example-bank"
    assert result.parser_id == "example-bank"
    assert result.statement.account_last4 == "6789"


def test_parse_with_explicit_parser(example_text: str) -> None:
    registry = ParserRegistry([ExampleBankParser()])

    result = parse(Document.from_text(example_text), parser="example-bank", registry=registry)

    assert result.bank == "Example Bank"


def test_parse_raises_for_unknown_and_unsupported() -> None:
    registry = ParserRegistry([ExampleBankParser()])

    with pytest.raises(UnknownParserError):
        parse(Document.from_text("Example Bank Statement"), parser="missing", registry=registry)
    with pytest.raises(UnsupportedStatementError):
        parse(Document.from_text("not a statement"), registry=registry)
