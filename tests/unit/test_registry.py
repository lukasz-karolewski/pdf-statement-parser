from __future__ import annotations

from importlib import metadata

import pytest

from pdf_statement_parser.document import Document
from pdf_statement_parser.models import Statement
from pdf_statement_parser.parsers.base import StatementParser
from pdf_statement_parser.registry import ParserRegistry, load_entry_point_parsers


class LowParser(StatementParser):
    id = "low"
    bank = "Bank"
    priority = 0

    def detect(self, doc: Document) -> float:
        return 0.60

    def parse(self, doc: Document) -> list[Statement]:
        return []


class HighParser(LowParser):
    id = "high"
    priority = 1


class RaisingParser(LowParser):
    id = "raising"

    def detect(self, doc: Document) -> float:
        raise RuntimeError("boom")


class EntryPoint:
    value = "tests:LowParser"

    def load(self) -> type[StatementParser]:
        return LowParser


def test_register_get_unregister_and_duplicates() -> None:
    registry = ParserRegistry()
    parser = registry.register(LowParser)

    assert registry.get("low") is parser
    assert "low" in registry
    with pytest.raises(ValueError, match="already registered"):
        registry.register(LowParser())
    registry.register(LowParser(), replace=True)
    registry.unregister("low")
    assert len(registry) == 0


def test_detection_threshold_and_priority_tie_break() -> None:
    registry = ParserRegistry([LowParser(), HighParser()])
    doc = Document.from_text("anything")

    ranked = registry.rank(doc)

    assert [d.parser.id for d in ranked] == ["high", "low"]
    assert registry.detect(doc, threshold=0.7) is None
    assert registry.detect(doc, threshold=0.5).parser.id == "high"  # type: ignore[union-attr]


def test_parser_raising_in_detect_scores_zero(caplog: pytest.LogCaptureFixture) -> None:
    registry = ParserRegistry([RaisingParser(), LowParser()])

    ranked = registry.rank(Document.from_text("anything"))

    assert ranked[-1].parser.id == "raising"
    assert ranked[-1].confidence == 0.0
    assert "raised during detect" in caplog.text


def test_entry_point_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_entry_points(*, group: str) -> list[EntryPoint]:
        assert group == "pdf_statement_parser.parsers"
        return [EntryPoint()]

    monkeypatch.setattr(metadata, "entry_points", fake_entry_points)

    assert load_entry_point_parsers() == [LowParser]
