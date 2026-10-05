from __future__ import annotations

from datetime import date
from decimal import Decimal

from pdf_statement_parser.models import AccountType, Statement, Transaction
from pdf_statement_parser.validation import reconcile


def test_reconcile_accepts_matching_statement() -> None:
    statement = Statement(
        bank="Example Bank",
        account_type=AccountType.CHECKING,
        account_number="1234",
        account_name="Checking",
        account_holder="Jane Doe",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        opening_balance=Decimal("100.00"),
        closing_balance=Decimal("130.00"),
        total_deposits=Decimal("50.00"),
        total_expenses=Decimal("20.00"),
        transactions=[
            Transaction(
                date=date(2026, 1, 5),
                description="Coffee",
                amount=Decimal("-20.00"),
                balance=Decimal("80.00"),
            ),
            Transaction(
                date=date(2026, 1, 10),
                description="Payroll",
                amount=Decimal("50.00"),
                balance=Decimal("130.00"),
            ),
        ],
    )

    assert reconcile(statement) == []


def test_reconcile_reports_mismatches() -> None:
    statement = Statement(
        bank="Example Bank",
        account_type=AccountType.CHECKING,
        account_number=None,
        period_start=date(2026, 2, 1),
        period_end=date(2026, 1, 1),
        total_deposits=Decimal("1.00"),
        transactions=[
            Transaction(date=date(2026, 1, 1), description="Deposit", amount=Decimal("2.00"))
        ],
    )

    checks = [issue.check for issue in reconcile(statement)]

    assert "missing_field" in checks
    assert "period" in checks
    assert "total_deposits" in checks


def test_reconcile_skips_fields_the_format_does_not_print() -> None:
    statement = Statement(
        bank="Example Bank",
        account_type=AccountType.CREDIT_CARD,
        account_number=None,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        extra={"not_printed": ["account_number"]},
    )

    assert reconcile(statement) == []
    statement.extra = {}
    assert [issue.check for issue in reconcile(statement)] == ["missing_field"]
