"""Extract account metadata and transactions from PDF bank statements."""

from statement_parser.__about__ import __version__
from statement_parser.api import detect, parse
from statement_parser.document import Document
from statement_parser.exceptions import (
    ParseError,
    StatementParserError,
    UnknownParserError,
    UnsupportedStatementError,
)
from statement_parser.models import AccountType, ParseResult, Statement, Transaction
from statement_parser.parsers.base import StatementParser
from statement_parser.registry import (
    Detection,
    ParserRegistry,
    default_registry,
    register_parser,
)
from statement_parser.validation import Issue, reconcile

__all__ = [
    "AccountType",
    "Detection",
    "Document",
    "Issue",
    "ParseError",
    "ParseResult",
    "ParserRegistry",
    "Statement",
    "StatementParser",
    "StatementParserError",
    "Transaction",
    "UnknownParserError",
    "UnsupportedStatementError",
    "__version__",
    "default_registry",
    "detect",
    "parse",
    "reconcile",
    "register_parser",
]
