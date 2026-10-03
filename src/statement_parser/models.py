"""Data model returned by every parser.

Sign convention: ``Transaction.amount`` is signed from the account holder's
point of view. Positive means money in (deposits, refunds, card payments),
negative means money out (purchases, withdrawals, fees, interest). This holds
for every account type, so ``total_deposits`` and ``total_expenses`` mean the
same thing for a checking account and for a credit card.

Balances are reported the way the bank prints them. For liability accounts
(credit cards, loans) a positive balance is the amount owed.
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any


class AccountType(str, enum.Enum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    LOAN = "loan"
    INVESTMENT = "investment"
    OTHER = "other"

    @property
    def is_liability(self) -> bool:
        return self in (AccountType.CREDIT_CARD, AccountType.LOAN)


@dataclass
class Transaction:
    date: date
    description: str
    amount: Decimal
    # Running balance after this transaction, when the statement prints one.
    balance: Decimal | None = None
    # Free-form section/category as labelled by the statement, e.g.
    # "purchase", "payment", "fee", "deposit", "withdrawal", "check".
    kind: str | None = None
    # Date the transaction posted, when it differs from ``date``.
    post_date: date | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_deposit(self) -> bool:
        return self.amount > 0

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(dataclasses.asdict(self))


@dataclass
class Statement:
    """One account over one statement period."""

    bank: str
    account_type: AccountType
    account_number: str | None
    period_start: date | None
    period_end: date | None
    # Product name as printed, e.g. "Chase Total Checking" or "Prime Visa".
    account_name: str | None = None
    # Name(s) of the account owner(s) as printed on the statement.
    account_holder: str | None = None
    currency: str = "USD"
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    # Non-negative totals for money in and money out during the period,
    # taken from the statement summary when it has one.
    total_deposits: Decimal | None = None
    total_expenses: Decimal | None = None
    transactions: list[Transaction] = field(default_factory=list)
    # Provider-specific fields (due date, credit limit, rewards, ...).
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def account_last4(self) -> str | None:
        if not self.account_number:
            return None
        digits = "".join(ch for ch in self.account_number if ch.isdigit())
        return digits[-4:] or None

    @property
    def transactions_deposits(self) -> Decimal:
        return sum((t.amount for t in self.transactions if t.amount > 0), Decimal("0"))

    @property
    def transactions_expenses(self) -> Decimal:
        return -sum((t.amount for t in self.transactions if t.amount < 0), Decimal("0"))

    @property
    def net_change(self) -> Decimal:
        return sum((t.amount for t in self.transactions), Decimal("0"))

    def to_dict(self, include_transactions: bool = True) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        if not include_transactions:
            data.pop("transactions")
        data["account_last4"] = self.account_last4
        return _to_jsonable(data)


@dataclass
class ParseResult:
    """Everything extracted from one document.

    Most PDFs describe a single account. Consolidated statements (for example a
    checking and a savings account mailed together) produce several entries in
    ``statements``; the first one is the primary account.
    """

    parser_id: str
    bank: str
    statements: list[Statement]
    source: str | None = None
    page_count: int | None = None

    @property
    def statement(self) -> Statement:
        if not self.statements:
            raise ValueError("document produced no statements")
        return self.statements[0]

    def to_dict(self, include_transactions: bool = True) -> dict[str, Any]:
        return {
            "source": self.source,
            "parser_id": self.parser_id,
            "bank": self.bank,
            "page_count": self.page_count,
            "statements": [s.to_dict(include_transactions) for s in self.statements],
        }


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value
