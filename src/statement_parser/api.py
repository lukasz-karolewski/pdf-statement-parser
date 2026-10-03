"""High-level entry points: ``detect`` and ``parse``."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from statement_parser.document import Document, Source
from statement_parser.exceptions import ParseError, StatementParserError, UnsupportedStatementError
from statement_parser.models import ParseResult
from statement_parser.registry import Detection, ParserRegistry, default_registry


@contextmanager
def _document(source: Source | Document, password: str | None) -> Iterator[Document]:
    if isinstance(source, Document):
        yield source
        return
    doc = Document.open(source, password=password)
    try:
        yield doc
    finally:
        doc.close()


def detect(
    source: Source | Document,
    *,
    registry: ParserRegistry | None = None,
    password: str | None = None,
) -> Detection | None:
    """Return the best parser for ``source``, or ``None`` if it is not recognised."""
    registry = registry or default_registry()
    with _document(source, password) as doc:
        return registry.detect(doc)


def parse(
    source: Source | Document,
    *,
    parser: str | None = None,
    registry: ParserRegistry | None = None,
    password: str | None = None,
) -> ParseResult:
    """Detect the statement type of ``source`` and extract it.

    Pass ``parser`` (a parser id) to skip detection.
    """
    registry = registry or default_registry()
    with _document(source, password) as doc:
        if parser is not None:
            chosen = registry.get(parser)
        else:
            detection = registry.detect(doc)
            if detection is None:
                raise UnsupportedStatementError(
                    f"no parser recognised {doc.name or 'document'}"
                )
            chosen = detection.parser
        try:
            statements = chosen.parse(doc)
        except StatementParserError:
            raise
        except Exception as exc:
            raise ParseError(f"{chosen.id} failed on {doc.name or 'document'}: {exc}") from exc
        return ParseResult(
            parser_id=chosen.id,
            bank=chosen.bank,
            statements=statements,
            source=doc.name,
            page_count=doc.page_count,
        )
