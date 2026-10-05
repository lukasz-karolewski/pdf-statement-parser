"""Parser registry and statement-type detection."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata

from pdf_statement_parser.document import Document
from pdf_statement_parser.exceptions import UnknownParserError
from pdf_statement_parser.parsers.base import DETECTION_THRESHOLD, StatementParser

log = logging.getLogger(__name__)

#: Entry point group third-party packages use to add parsers.
ENTRY_POINT_GROUP = "pdf_statement_parser.parsers"

#: Parsers shipped with the library, as "module:Class" paths.
BUILTIN_PARSERS = (
    "pdf_statement_parser.parsers.apple.card:AppleCardParser",
    "pdf_statement_parser.parsers.chase.credit_card:ChaseCreditCardParser",
    "pdf_statement_parser.parsers.chase.deposit:ChaseDepositParser",
)


@dataclass(frozen=True)
class Detection:
    parser: StatementParser
    confidence: float

    @property
    def parser_id(self) -> str:
        return self.parser.id


class ParserRegistry:
    def __init__(self, parsers: Iterable[StatementParser] = ()) -> None:
        self._parsers: dict[str, StatementParser] = {}
        for parser in parsers:
            self.register(parser)

    def register(
        self, parser: StatementParser | type[StatementParser], *, replace: bool = False
    ) -> StatementParser:
        instance = parser() if isinstance(parser, type) else parser
        if instance.id in self._parsers and not replace:
            raise ValueError(f"parser id {instance.id!r} is already registered")
        self._parsers[instance.id] = instance
        return instance

    def unregister(self, parser_id: str) -> None:
        self._parsers.pop(parser_id, None)

    def get(self, parser_id: str) -> StatementParser:
        try:
            return self._parsers[parser_id]
        except KeyError:
            known = ", ".join(sorted(self._parsers)) or "none"
            raise UnknownParserError(
                f"unknown parser {parser_id!r} (registered: {known})"
            ) from None

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._parsers.values())

    def __len__(self) -> int:
        return len(self._parsers)

    def __contains__(self, parser_id: object) -> bool:
        return parser_id in self._parsers

    def rank(self, doc: Document) -> list[Detection]:
        """Every parser's confidence for ``doc``, best first."""
        results = []
        for parser in self._parsers.values():
            try:
                score = float(parser.detect(doc))
            except Exception:
                log.exception("parser %s raised during detect()", parser.id)
                score = 0.0
            results.append(Detection(parser, max(0.0, min(1.0, score))))
        results.sort(key=lambda d: (d.confidence, d.parser.priority), reverse=True)
        return results

    def detect(self, doc: Document, threshold: float = DETECTION_THRESHOLD) -> Detection | None:
        """The best matching parser, or ``None`` if nothing clears ``threshold``."""
        ranked = self.rank(doc)
        if ranked and ranked[0].confidence >= threshold:
            return ranked[0]
        return None


def _load(path: str) -> type[StatementParser]:
    module_name, _, attr = path.partition(":")
    module = __import__(module_name, fromlist=[attr])
    cls: type[StatementParser] = getattr(module, attr)
    return cls


def load_entry_point_parsers() -> list[type[StatementParser]]:
    found = []
    for ep in metadata.entry_points(group=ENTRY_POINT_GROUP):
        try:
            found.append(ep.load())
        except Exception:
            log.exception("failed to load parser plugin %s", ep.value)
    return found


_default: ParserRegistry | None = None


def default_registry() -> ParserRegistry:
    """Registry with the built-in parsers plus any installed plugins."""
    global _default
    if _default is None:
        registry = ParserRegistry()
        for path in BUILTIN_PARSERS:
            registry.register(_load(path))
        for cls in load_entry_point_parsers():
            if cls.id in registry:
                log.warning("plugin %s shadows parser id %r; skipping", cls, cls.id)
                continue
            registry.register(cls)
        _default = registry
    return _default


def register_parser(cls: type[StatementParser]) -> type[StatementParser]:
    """Class decorator that adds a parser to the default registry."""
    default_registry().register(cls)
    return cls
