"""Consistency checks for parsed statements.

These checks are how we know a parser works without a human reading each PDF:
the statement's own summary must agree with the transactions we extracted.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pdf_statement_parser.models import Statement

TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class Issue:
    check: str
    message: str
    expected: Decimal | None = None
    actual: Decimal | None = None


def reconcile(statement: Statement) -> list[Issue]:
    """Return every inconsistency found; an empty list means it reconciles."""
    issues: list[Issue] = []

    for name in ("account_number", "period_start", "period_end"):
        if getattr(statement, name) is None:
            issues.append(Issue("missing_field", f"{name} is missing"))
    if statement.period_start and statement.period_end:
        if statement.period_start > statement.period_end:
            issues.append(Issue("period", "period_start is after period_end"))
        for t in statement.transactions:
            # Card transactions may predate the period by a few days.
            if (statement.period_start - t.date).days > 45 or t.date > statement.period_end:
                issues.append(
                    Issue("transaction_date", f"{t.date} outside period: {t.description[:40]}")
                )

    if statement.total_deposits is not None:
        _compare(
            issues, "total_deposits", statement.total_deposits, statement.transactions_deposits
        )
    if statement.total_expenses is not None:
        _compare(
            issues, "total_expenses", statement.total_expenses, statement.transactions_expenses
        )

    if statement.opening_balance is not None and statement.closing_balance is not None:
        net = statement.net_change
        if statement.account_type.is_liability:
            expected = statement.opening_balance - net
        else:
            expected = statement.opening_balance + net
        _compare(issues, "balance_roll", statement.closing_balance, expected)

    running = [t for t in statement.transactions if t.balance is not None]
    if running and statement.opening_balance is not None:
        balance = statement.opening_balance
        for t in statement.transactions:
            balance += t.amount
            if t.balance is not None and abs(t.balance - balance) > TOLERANCE:
                issues.append(
                    Issue("running_balance", f"{t.date} {t.description[:40]}", t.balance, balance)
                )
                break

    return issues


def _compare(issues: list[Issue], check: str, expected: Decimal, actual: Decimal) -> None:
    if abs(expected - actual) > TOLERANCE:
        issues.append(Issue(check, f"expected {expected}, got {actual}", expected, actual))
