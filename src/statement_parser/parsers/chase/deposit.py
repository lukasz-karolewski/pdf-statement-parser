"""Chase deposit account statements (checking, savings). STUB: to be implemented."""

from __future__ import annotations

from statement_parser.document import Document
from statement_parser.models import Statement
from statement_parser.parsers.base import StatementParser


class ChaseDepositParser(StatementParser):
    id = "chase-deposit"
    bank = "Chase"
    description = "Chase checking and savings, personal and business, including consolidated statements"

    def detect(self, doc: Document) -> float:
        return 0.0

    def parse(self, doc: Document) -> list[Statement]:
        raise NotImplementedError
