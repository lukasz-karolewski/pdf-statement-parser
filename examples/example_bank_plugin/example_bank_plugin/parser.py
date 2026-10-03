from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from pdf_statement_parser.document import Document
from pdf_statement_parser.models import AccountType, Statement, Transaction
from pdf_statement_parser.parsers.base import StatementParser


class ExampleBankParser(StatementParser):
    id = "example-bank"
    bank = "Example Bank"
    description = "Example parser for synthetic text statements"

    def detect(self, doc: Document) -> float:
        return 0.95 if "Example Bank Statement" in doc.text else 0.0

    def parse(self, doc: Document) -> list[Statement]:
        text = doc.text
        account_number = _match(text, r"^Account:\s*(.+)$")
        start_text, end_text = _match(
            text, r"^Period:\s*(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})$", groups=2
        )
        transactions = []
        for found in re.finditer(
            r"^(\d{4}-\d{2}-\d{2})\s+(.+?)\s+(-?\d+\.\d{2})$", text, re.MULTILINE
        ):
            transactions.append(
                Transaction(
                    date=date.fromisoformat(found.group(1)),
                    description=found.group(2),
                    amount=Decimal(found.group(3)),
                )
            )
        return [
            Statement(
                bank=self.bank,
                account_type=AccountType.CHECKING,
                account_number=account_number,
                period_start=date.fromisoformat(start_text),
                period_end=date.fromisoformat(end_text),
                opening_balance=Decimal(_match(text, r"^Opening balance:\s*(.+)$")),
                closing_balance=Decimal(_match(text, r"^Closing balance:\s*(.+)$")),
                transactions=transactions,
            )
        ]


def _match(text: str, pattern: str, *, groups: int = 1) -> str | tuple[str, ...]:
    found = re.search(pattern, text, re.MULTILINE)
    if not found:
        raise ValueError(f"missing {pattern}")
    if groups == 1:
        return found.group(1).strip()
    return tuple(found.group(index).strip() for index in range(1, groups + 1))
