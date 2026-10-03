from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pdf_statement_parser.models import AccountType, ParseResult, Statement, Transaction


def test_transaction_and_statement_to_dict_serialise_json_values() -> None:
    tx = Transaction(date=date(2026, 1, 2), description="Deposit", amount=Decimal("10.50"))
    statement = Statement(
        bank="Example Bank",
        account_type=AccountType.CHECKING,
        account_number="abc-1234",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        opening_balance=Decimal("1.00"),
        closing_balance=Decimal("11.50"),
        transactions=[tx],
    )

    data = statement.to_dict()

    assert data["account_type"] == "checking"
    assert data["account_last4"] == "1234"
    assert data["opening_balance"] == "1.00"
    assert data["transactions"][0]["amount"] == "10.50"


def test_statement_computed_totals_and_primary_statement() -> None:
    statement = Statement(
        bank="Example Bank",
        account_type=AccountType.CREDIT_CARD,
        account_number="9999",
        period_start=None,
        period_end=None,
        transactions=[
            Transaction(date=date(2026, 1, 1), description="Purchase", amount=Decimal("-3.25")),
            Transaction(date=date(2026, 1, 2), description="Refund", amount=Decimal("1.25")),
        ],
    )
    result = ParseResult(parser_id="example", bank="Example Bank", statements=[statement])

    assert statement.transactions_deposits == Decimal("1.25")
    assert statement.transactions_expenses == Decimal("3.25")
    assert statement.net_change == Decimal("-2.00")
    assert result.statement is statement
    assert statement.account_type.is_liability


def test_empty_parse_result_has_no_primary_statement() -> None:
    result = ParseResult(parser_id="example", bank="Example Bank", statements=[])

    with pytest.raises(ValueError, match="no statements"):
        _ = result.statement
