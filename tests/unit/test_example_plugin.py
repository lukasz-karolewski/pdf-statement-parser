from __future__ import annotations

from examples.example_bank_plugin.example_bank_plugin.parser import ExampleBankParser

from pdf_statement_parser.document import Document


def test_example_plugin_parser_on_synthetic_document() -> None:
    doc = Document.from_text(
        """Example Bank Statement
Account: 00001234
Period: 2026-01-01 to 2026-01-31
Opening balance: 10.00
Closing balance: 7.50
Transactions
2026-01-02 Coffee -2.50
"""
    )
    parser = ExampleBankParser()

    result = parser.parse(doc)

    assert parser.detect(doc) == 0.95
    assert result[0].account_last4 == "1234"
    assert result[0].transactions[0].description == "Coffee"
