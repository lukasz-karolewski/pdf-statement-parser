from __future__ import annotations

from pathlib import Path

import pytest

from pdf_statement_parser.document import Document
from pdf_statement_parser.models import AccountType, Statement
from pdf_statement_parser.parsers.chase.deposit import ChaseDepositParser
from pdf_statement_parser.validation import reconcile

FIXTURES = Path(__file__).parents[2] / "fixtures" / "chase" / "deposit"


def load_doc(name: str) -> Document:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return Document.from_text(text.split("\f"), name=name)


def assert_reconciles(doc: Document) -> list[Statement]:
    statements = ChaseDepositParser().parse(doc)
    for statement in statements:
        assert reconcile(statement) == []
    return statements


def test_detects_deposit_statement() -> None:
    parser = ChaseDepositParser()
    assert parser.detect(load_doc("personal_checking.txt")) >= 0.9


@pytest.mark.parametrize(
    "fixture",
    [
        "personal_checking.txt",
        "business_sections.txt",
        "multipage_continuation.txt",
        "zero_activity.txt",
    ],
)
def test_single_account_fixtures_reconcile(fixture: str) -> None:
    statements = assert_reconciles(load_doc(fixture))
    assert len(statements) == 1
    assert statements[0].bank == "Chase"
    assert statements[0].account_holder
    assert statements[0].account_number


def test_personal_checking_running_balance_and_year_boundary() -> None:
    statement = assert_reconciles(load_doc("personal_checking.txt"))[0]
    assert statement.account_type == AccountType.CHECKING
    assert statement.period_start and statement.period_start.year == 2025
    assert statement.period_end and statement.period_end.year == 2026
    assert [transaction.date.year for transaction in statement.transactions[:2]] == [2025, 2026]
    assert statement.transactions[-1].kind == "fee"


def test_business_sections_multiline_and_checks() -> None:
    statement = assert_reconciles(load_doc("business_sections.txt"))[0]
    assert statement.account_name == "Chase Business Complete Checking"
    check = next(
        transaction for transaction in statement.transactions if transaction.kind == "check"
    )
    assert check.extra["check_number"] == "1001"
    vendor = next(
        transaction
        for transaction in statement.transactions
        if "SAMPLE VENDOR" in transaction.description
    )
    assert "Invoice 100 continuation text" in vendor.description


def test_consolidated_statement_with_savings() -> None:
    statements = assert_reconciles(load_doc("consolidated_savings.txt"))
    assert [statement.account_type for statement in statements] == [
        AccountType.CHECKING,
        AccountType.CHECKING,
        AccountType.SAVINGS,
    ]
    assert [statement.account_last4 for statement in statements] == ["3333", "7777", "3389"]
    assert statements[2].extra["interest_paid"] == "1.00"
    assert all(not statement.warnings for statement in statements)


def test_consolidated_count_mismatch_warns() -> None:
    statements = assert_reconciles(load_doc("consolidated_count_mismatch.txt"))
    assert len(statements) == 1
    assert statements[0].warnings == [
        "parsed account count 1 differs from consolidated summary count 2"
    ]


def test_holder_comes_from_mailing_block_without_marker_noise() -> None:
    statement = assert_reconciles(load_doc("holder_marker_noise.txt"))[0]
    assert statement.account_holder == "JANE Q SAMPLE TRUSTEE, OR JOHN SAMPLE TRUSTEE"
    assert "*" not in statement.account_holder
    assert "SM" not in statement.account_holder


def test_multipage_continuation_description() -> None:
    statement = assert_reconciles(load_doc("multipage_continuation.txt"))[0]
    assert "continued memo line" in statement.transactions[-1].description


def test_zero_activity_has_no_transactions() -> None:
    statement = assert_reconciles(load_doc("zero_activity.txt"))[0]
    assert statement.transactions == []


def test_detect_rejects_credit_card_like_text() -> None:
    assert ChaseDepositParser().detect(load_doc("credit_card_like.txt")) == 0.0
