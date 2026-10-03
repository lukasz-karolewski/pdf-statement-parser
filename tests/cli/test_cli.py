from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import ExampleBankParser

import pdf_statement_parser.api as api_module
import pdf_statement_parser.cli as cli
from pdf_statement_parser.document import Document
from pdf_statement_parser.registry import ParserRegistry


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch: pytest.MonkeyPatch, example_registry: ParserRegistry) -> None:
    monkeypatch.setattr(cli, "default_registry", lambda: example_registry)
    monkeypatch.setattr(api_module, "default_registry", lambda: example_registry)


def test_parse_json_csv_table_and_outfile(
    example_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["parse", str(example_pdf)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["parser_id"] == "example-bank"
    assert data[0]["statements"][0]["transactions"][0]["amount"] == "-20.00"

    out = tmp_path / "parsed.csv"
    assert cli.main(["parse", str(example_pdf), "--format", "csv", "-o", str(out)]) == 0
    assert "opening_balance" in out.read_text(encoding="utf-8")
    assert "100.00" in out.read_text(encoding="utf-8")

    assert cli.main(["parse", str(example_pdf), "--format", "table", "--no-transactions"]) == 0
    assert "Example Bank" in capsys.readouterr().out


def test_transactions_csv_and_json(example_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["transactions", str(example_pdf)]) == 0
    assert "Coffee" in capsys.readouterr().out

    assert cli.main(["transactions", str(example_pdf), "--format", "json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[1]["kind"] == "deposit"
    assert rows[1]["amount"] == "50.00"


def test_detect_best_and_all(example_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["detect", str(example_pdf)]) == 0
    assert "example-bank 0.95" in capsys.readouterr().out

    assert cli.main(["detect", str(example_pdf), "--all"]) == 0
    assert "example-bank: 0.95" in capsys.readouterr().out


def test_validate_reports_ok_and_writes_report(
    example_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "report.json"

    assert cli.main(["validate", str(example_pdf), "-j", "1", "--report", str(report)]) == 0

    output = capsys.readouterr().out
    assert "ok" in output
    assert "summary: 1 files, ok=1" in output
    assert json.loads(report.read_text(encoding="utf-8"))[0]["status"] == "ok"


def test_parsers_lists_registered_parser(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["parsers"]) == 0
    assert "example-bank" in capsys.readouterr().out


def test_dump_prints_extracted_text(example_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["dump", str(example_pdf), "--page", "1"]) == 0
    assert "Example Bank Statement" in capsys.readouterr().out

    assert cli.main(["dump", str(example_pdf)]) == 0
    assert "--- page 1 ---" in capsys.readouterr().out


def test_batch_errors_continue(example_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = example_pdf.with_name("missing.pdf")

    assert cli.main(["parse", str(example_pdf), str(missing)]) == 1

    captured = capsys.readouterr()
    assert "example-bank" in captured.out
    assert "path does not exist" in captured.err


def test_python_module_entrypoint_imports() -> None:
    with Document.from_text("Example Bank Statement") as doc:
        assert ExampleBankParser().detect(doc) > 0
