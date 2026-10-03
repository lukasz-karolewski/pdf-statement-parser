from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pdf_statement_parser.document import Document
from pdf_statement_parser.models import AccountType, Statement
from pdf_statement_parser.parsers.chase.credit_card import ChaseCreditCardParser
from pdf_statement_parser.validation import reconcile

FIXTURES = Path(__file__).parents[2] / "fixtures" / "chase" / "credit_card"


def load_doc(name: str) -> Document:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return Document.from_text(text.split("\f"), name=name)


def assert_reconciles(name: str) -> Statement:
    statement = ChaseCreditCardParser().parse(load_doc(name))[0]
    assert reconcile(statement) == []
    assert not statement.warnings
    return statement


@pytest.mark.parametrize(
    "fixture",
    [
        "basic_statement.txt",
        "multipage_continuation.txt",
        "refund_statement.txt",
        "fees_interest.txt",
        "zero_activity.txt",
        "year_boundary_masked.txt",
    ],
)
def test_credit_card_fixtures_reconcile(fixture: str) -> None:
    statement = assert_reconciles(fixture)
    assert statement.bank == "Chase"
    assert statement.account_type == AccountType.CREDIT_CARD
    assert statement.account_holder == "JANE Q SAMPLE"
    assert statement.account_number
    assert statement.account_name


def test_basic_statement_metadata_and_extra_fields() -> None:
    statement = assert_reconciles("basic_statement.txt")
    assert statement.account_name == "Freedom Unlimited"
    assert statement.account_number == "4000123456789010"
    assert statement.period_start == date(2026, 3, 2)
    assert statement.period_end == date(2026, 4, 1)
    assert statement.opening_balance == Decimal("100.00")
    assert statement.closing_balance == Decimal("60.00")
    assert statement.total_deposits == Decimal("100.00")
    assert statement.total_expenses == Decimal("60.00")
    assert statement.extra["payment_due_date"] == date(2026, 4, 26)
    assert statement.extra["minimum_payment_due"] == Decimal("35.00")
    assert statement.extra["credit_limit"] == Decimal("5000.00")


def test_summary_amounts_tolerate_glued_right_column_text() -> None:
    statement = assert_reconciles("basic_statement.txt")
    assert statement.opening_balance == Decimal("100.00")
    assert statement.total_deposits == Decimal("100.00")


def test_multipage_continuation_and_order_number() -> None:
    statement = assert_reconciles("multipage_continuation.txt")
    assert statement.account_name == "Prime Visa"
    assert len(statement.transactions) == 3
    bookstore = statement.transactions[1]
    assert bookstore.extra["order_number"] == "111-2222222-3333333"
    assert "foreign currency conversion detail" in statement.transactions[2].description


def test_refund_in_purchase_section_splits_summary_net_total() -> None:
    statement = assert_reconciles("refund_statement.txt")
    assert statement.total_deposits == Decimal("25.00")
    assert statement.total_expenses == Decimal("50.00")
    assert [transaction.amount for transaction in statement.transactions] == [
        Decimal("-50.00"),
        Decimal("25.00"),
    ]
    assert statement.transactions[1].kind == "refund"


def test_fees_and_interest_sections() -> None:
    statement = assert_reconciles("fees_interest.txt")
    assert statement.account_name == "United MileagePlus"
    assert [transaction.kind for transaction in statement.transactions] == ["fee", "interest"]
    assert statement.total_expenses == Decimal("40.23")


def test_zero_activity_and_masked_account_number() -> None:
    statement = assert_reconciles("zero_activity.txt")
    assert statement.account_name == "Southwest Rapid Rewards"
    assert statement.account_number == "XXXXXXXXXXXX9050"
    assert statement.transactions == []


def test_year_boundary_dates() -> None:
    statement = assert_reconciles("year_boundary_masked.txt")
    assert statement.account_name == "Ultimate Rewards"
    assert [transaction.date for transaction in statement.transactions] == [
        date(2025, 12, 31),
        date(2026, 1, 2),
    ]


def test_detects_credit_card_and_rejects_deposit_text() -> None:
    parser = ChaseCreditCardParser()
    assert parser.detect(load_doc("basic_statement.txt")) >= 0.9
    assert parser.detect(load_doc("deposit_statement_like.txt")) == 0.0
