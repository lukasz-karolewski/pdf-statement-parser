class StatementParserError(Exception):
    """Base class for every error raised by this library."""


class UnsupportedStatementError(StatementParserError):
    """No registered parser recognised the document."""


class ParseError(StatementParserError):
    """A parser recognised the document but could not extract it."""


class UnknownParserError(StatementParserError):
    """A parser id was requested that is not registered."""
