"""Extract account metadata and transactions from PDF bank statements."""

from pdf_statement_parser.__about__ import __version__
from pdf_statement_parser.api import detect, parse
from pdf_statement_parser.document import Document
from pdf_statement_parser.exceptions import (
    ParseError,
    StatementParserError,
    UnknownParserError,
    UnsupportedStatementError,
)
from pdf_statement_parser.models import AccountType, ParseResult, Statement, Transaction
from pdf_statement_parser.parsers.base import StatementParser
from pdf_statement_parser.registry import (
    Detection,
    ParserRegistry,
    default_registry,
    register_parser,
)
from pdf_statement_parser.validation import Issue, reconcile

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
