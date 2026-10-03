"""Statement parsers. Each bank lives in its own subpackage."""

from statement_parser.parsers.base import DETECTION_THRESHOLD, StatementParser

__all__ = ["DETECTION_THRESHOLD", "StatementParser"]
