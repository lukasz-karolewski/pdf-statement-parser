from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pdf_statement_parser.document import Document
from pdf_statement_parser.models import AccountType, Statement
from pdf_statement_parser.parsers.apple.card import AppleCardParser, _parse_period
from pdf_statement_parser.parsers.chase.credit_card import ChaseCreditCardParser
from pdf_statement_parser.registry import default_registry
from pdf_statement_parser.validation import reconcile

FIXTURES = Path(__file__).parents[2] / "fixtures" / "apple" / "card"
CHASE_FIXTURES = Path(__file__).parents[2] / "fixtures" / "chase" / "credit_card"
APPLE_FIXTURES = ["co_owned_statement.txt", "new_installment_prior_layout.txt", "zero_activity.txt"]


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_doc(name: str, text: str | None = None) -> Document:
    return Document.from_text((text or load_text(name)).split("\f"), name=name)


def parse(name: str, text: str | None = None) -> Statement:
    return AppleCardParser().parse(load_doc(name, text))[0]


@pytest.mark.parametrize("fixture", APPLE_FIXTURES)
def test_fixtures_detect_and_reconcile(fixture: str) -> None:
    doc = load_doc(fixture)
    detection = default_registry().detect(doc)
    assert detection is not None
    assert detection.parser_id == "apple-card"
    assert ChaseCreditCardParser().detect(doc) == 0.0

    statement = AppleCardParser().parse(doc)[0]
    assert reconcile(statement) == []
    assert statement.warnings == []
    assert statement.bank == "Apple"
    assert statement.account_type == AccountType.CREDIT_CARD
    assert statement.account_name == "Apple Card"
    assert statement.account_number is None
    assert statement.extra["not_printed"] == ["account_number"]


@pytest.mark.parametrize("fixture", sorted(p.name for p in CHASE_FIXTURES.glob("*.txt")))
def test_chase_fixtures_are_not_apple_card(fixture: str) -> None:
    text = (CHASE_FIXTURES / fixture).read_text(encoding="utf-8")
    assert AppleCardParser().detect(Document.from_text(text.split("\f"))) == 0.0


def test_co_owned_statement_metadata() -> None:
    statement = parse("co_owned_statement.txt")
    assert statement.account_holder == "Jane Sample, John Sample"
    assert statement.period_start == date(2025, 3, 1)
    assert statement.period_end == date(2025, 3, 31)
    assert statement.opening_balance == Decimal("100.00")
    assert statement.closing_balance == Decimal("95.60")
    # Payment 100.00 plus the 20.00 return.
    assert statement.total_deposits == Decimal("120.00")
    # Net charges 75.60, the return added back, and the 20.00 installment.
    assert statement.total_expenses == Decimal("115.60")
    extra = statement.extra
    assert extra["payment_due_date"] == date(2025, 4, 30)
    assert extra["minimum_payment_due"] == Decimal("30.00")
    assert extra["previous_total_balance"] == Decimal("480.00")
    assert extra["total_balance"] == Decimal("455.60")
    assert extra["installments_due"] == Decimal("20.00")
    assert extra["installments_remaining"] == Decimal("380.00")
    assert extra["daily_cash"] == Decimal("2.45")
    assert extra["apr"] == Decimal("19.99")
    assert extra["co_owners"] == ["Jane Sample", "John Sample"]
    assert extra["issuer"] == "Example Bank USA, Sample City Branch"
    assert "new_installment_financed" not in extra


def test_co_owned_statement_transactions() -> None:
    statement = parse("co_owned_statement.txt")
    kinds = [(t.kind, t.amount) for t in statement.transactions]
    assert kinds == [
        ("payment", Decimal("100.00")),
        ("purchase", Decimal("-5.00")),
        ("purchase", Decimal("-50.00")),
        ("refund", Decimal("20.00")),
        ("daily_cash_adjustment", Decimal("-0.60")),
        ("purchase", Decimal("-40.00")),
        ("installment", Decimal("-20.00")),
    ]
    payment, coffee, _, refund, adjustment, grocery, installment = statement.transactions
    assert payment.extra == {"cardholder": "Jane Sample"}
    assert payment.description.startswith("ACH Deposit Internet transfer")
    assert coffee.extra["daily_cash_rate"] == Decimal("2")
    assert coffee.extra["daily_cash"] == Decimal("0.10")
    assert coffee.extra["daily_cash_details"] == [
        {"label": "Promo Daily Cash", "rate": Decimal("1"), "amount": Decimal("0.05")}
    ]
    assert refund.description.endswith("(RETURN)")
    assert "daily_cash" not in refund.extra
    assert adjustment.date == refund.date
    assert adjustment.description.startswith("Daily Cash Adjustment: APPLE.COM/BILL")
    assert adjustment.extra["daily_cash_rate"] == Decimal("-3")
    assert grocery.extra["cardholder"] == "John Sample"
    assert installment.date == date(2025, 3, 31)
    assert (
        installment.description == "Apple Card Monthly Installment: Apple Online Store Cupertino CA"
    )
    assert installment.extra == {
        "purchase_date": date(2024, 10, 28),
        "transaction_id": "abc123def456",
        "final_installment": date(2026, 10, 31),
        "financed": Decimal("480.00"),
        "paid_to_date": Decimal("100.00"),
        "remaining": Decimal("380.00"),
        "cardholder": "John Sample",
    }


def test_new_installment_plan_and_prior_labels() -> None:
    statement = parse("new_installment_prior_layout.txt")
    assert statement.account_holder == "Jane Sample"
    assert "co_owners" not in statement.extra
    assert statement.opening_balance == Decimal("50.00")
    assert statement.closing_balance == Decimal("65.00")
    assert statement.extra["previous_total_balance"] == Decimal("60.00")
    assert statement.extra["total_balance"] == Decimal("640.00")
    assert statement.extra["new_installment_financed"] == Decimal("600.00")
    assert statement.extra["daily_cash"] == Decimal("18.90")

    new_plan, final_plan = [t for t in statement.transactions if t.kind == "installment"]
    assert new_plan.amount == Decimal("-25.00")
    assert new_plan.extra["daily_cash"] == Decimal("18.00")
    assert new_plan.extra["final_installment"] == date(2023, 11, 30)
    assert final_plan.amount == Decimal("-10.00")
    assert final_plan.extra["final_installment"] == date(2021, 12, 31)


def test_zero_activity_first_statement() -> None:
    statement = parse("zero_activity.txt")
    assert statement.period_start == date(2021, 10, 22)
    assert statement.transactions == []
    assert statement.opening_balance == statement.closing_balance == Decimal("0.00")
    assert statement.total_deposits == statement.total_expenses == Decimal("0.00")
    assert "installments_due" not in statement.extra


def test_missing_transaction_is_reported() -> None:
    text = load_text("co_owned_statement.txt").replace(
        "03/15/2025 SAMPLE GROCERY 9 ELM ST SPRINGFIELD 12345 IL USA 2% $0.80 $40.00\n", ""
    )
    statement = parse("co_owned_statement.txt", text)
    assert any("charges, credits and returns" in w for w in statement.warnings)
    assert {issue.check for issue in reconcile(statement)} == {"total_expenses", "balance_roll"}


def test_total_balance_mismatch_is_reported() -> None:
    text = load_text("co_owned_statement.txt").replace(
        "Total Balance $455.60", "Total Balance $465.60"
    )
    statement = parse("co_owned_statement.txt", text)
    assert statement.warnings == ["total balance 465.60 differs from rolled-forward 455.60"]


def test_period_crossing_new_year() -> None:
    start, end = _parse_period("Jane Sample, jane@example.com Dec 15 — Jan 14, 2026")
    assert start == date(2025, 12, 15)
    assert end == date(2026, 1, 14)
