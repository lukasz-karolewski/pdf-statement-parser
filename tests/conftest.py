from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fpdf import FPDF

from pdf_statement_parser.document import Document
from pdf_statement_parser.models import AccountType, Rewards, Statement, Transaction
from pdf_statement_parser.parsers.base import StatementParser
from pdf_statement_parser.registry import ParserRegistry

EXAMPLE_TEXT = """Example Bank Statement
Account: 123456789
Name: Example Checking
Holder: Jane Doe
Period: 01/01/2026 - 01/31/2026
Opening: 100.00
Deposits: 50.00
Expenses: 20.00
Closing: 130.00
Rewards Program: Example Rewards
Rewards Unit: points
Rewards Opening: 1000
Rewards Closing: 1450
Rewards Earned: 500
Rewards Redeemed: -50
Rewards Difference: 0
Transactions:
01/05/26 Coffee -20.00 80.00 purchase
01/10/26 Payroll 50.00 130.00 deposit
"""


class ExampleBankParser(StatementParser):
    id = "example-bank"
    bank = "Example Bank"
    description = "Synthetic example bank statements"
    priority = 10

    def detect(self, doc: Document) -> float:
        return 0.95 if "Example Bank Statement" in doc.text else 0.0

    def parse(self, doc: Document) -> list[Statement]:
        text = doc.text

        def match(pattern: str) -> str:
            found = re.search(pattern, text, re.MULTILINE)
            if not found:
                raise ValueError(pattern)
            return found.group(1).strip()

        transactions: list[Transaction] = []
        for found in re.finditer(
            r"^(\d{2}/\d{2}/\d{2})\s+(.+?)\s+(-?\d+\.\d{2})\s+(-?\d+\.\d{2})\s+(\w+)$",
            text,
            re.MULTILINE,
        ):
            mm, dd, yy = (int(part) for part in found.group(1).split("/"))
            transactions.append(
                Transaction(
                    date=date(2000 + yy, mm, dd),
                    description=found.group(2),
                    amount=Decimal(found.group(3)),
                    balance=Decimal(found.group(4)),
                    kind=found.group(5),
                )
            )
        return [
            Statement(
                bank=self.bank,
                account_type=AccountType.CHECKING,
                account_number=match(r"^Account:\s*(.+)$"),
                account_name=match(r"^Name:\s*(.+)$"),
                account_holder=match(r"^Holder:\s*(.+)$"),
                period_start=date(2026, 1, 1),
                period_end=date(2026, 1, 31),
                opening_balance=Decimal(match(r"^Opening:\s*(.+)$")),
                closing_balance=Decimal(match(r"^Closing:\s*(.+)$")),
                total_deposits=Decimal(match(r"^Deposits:\s*(.+)$")),
                total_expenses=Decimal(match(r"^Expenses:\s*(.+)$")),
                transactions=transactions,
                rewards=Rewards(
                    program=match(r"^Rewards Program:\s*(.+)$"),
                    unit=match(r"^Rewards Unit:\s*(.+)$"),
                    opening_balance=int(match(r"^Rewards Opening:\s*(.+)$")),
                    closing_balance=int(match(r"^Rewards Closing:\s*(.+)$")),
                    earned=int(match(r"^Rewards Earned:\s*(.+)$")),
                    redeemed=int(match(r"^Rewards Redeemed:\s*(.+)$")),
                    difference=int(match(r"^Rewards Difference:\s*(.+)$")),
                ),
            )
        ]


@pytest.fixture
def example_text() -> str:
    return EXAMPLE_TEXT


@pytest.fixture
def example_registry() -> ParserRegistry:
    return ParserRegistry([ExampleBankParser()])


@pytest.fixture
def example_pdf(tmp_path: Path, example_text: str) -> Path:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in example_text.splitlines():
        pdf.cell(0, 8, text=line, new_x="LMARGIN", new_y="NEXT")
    path = tmp_path / "example-bank.pdf"
    pdf.output(path)
    return path
