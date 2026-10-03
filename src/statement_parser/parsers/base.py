"""Base class for statement parsers (plugins).

A parser does two things:

``detect(doc)``
    Return a confidence between 0.0 and 1.0 that ``doc`` is a statement this
    parser understands. Keep it cheap: look at the first page or two. Return
    0.0 for anything else. The registry runs every parser's ``detect`` and
    picks the highest score at or above ``DETECTION_THRESHOLD``.

``parse(doc)``
    Return a list of :class:`~statement_parser.models.Statement`, one per
    account in the document. Raise :class:`~statement_parser.exceptions.ParseError`
    when the document cannot be read. Put non-fatal problems in
    ``Statement.warnings``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from statement_parser.document import Document
from statement_parser.models import Statement

DETECTION_THRESHOLD = 0.5


class StatementParser(ABC):
    #: Unique, stable id such as ``"chase-credit-card"``. Used on the CLI.
    id: ClassVar[str]
    #: Human-readable bank name stored on every Statement.
    bank: ClassVar[str]
    #: One-line description shown by ``statement-parser parsers``.
    description: ClassVar[str] = ""
    #: Tie-breaker when two parsers report the same confidence. Higher wins.
    priority: ClassVar[int] = 0

    @abstractmethod
    def detect(self, doc: Document) -> float: ...

    @abstractmethod
    def parse(self, doc: Document) -> list[Statement]: ...

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={self.id!r}>"
