"""Chase credit card statements. STUB: to be implemented."""

from __future__ import annotations

from statement_parser.document import Document
from statement_parser.models import Statement
from statement_parser.parsers.base import StatementParser


class ChaseCreditCardParser(StatementParser):
    id = "chase-credit-card"
    bank = "Chase"
    description = "Chase personal credit cards (Freedom, Sapphire, Prime Visa, United, Southwest, ...)"

    def detect(self, doc: Document) -> float:
        return 0.0

    def parse(self, doc: Document) -> list[Statement]:
        raise NotImplementedError
