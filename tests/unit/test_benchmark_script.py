from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark.py"


def run(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return result.stdout


def test_benchmark_reports_phases_and_writes_json(example_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "timings.json"

    stdout = run(str(example_pdf), str(example_pdf), "--warmup", "0", "--json", str(out))

    assert "Files: 2   pages: 2" in stdout
    for phase in ("open", "extract", "detect", "parse", "total"):
        assert f"  {phase} " in stdout
    rows = json.loads(out.read_text())
    assert [row["file"] for row in rows] == [str(example_pdf)] * 2
    assert all(row["pages"] == 1 and row["total"] > 0 for row in rows)


def test_benchmark_memory_and_profile_modes(example_pdf: Path) -> None:
    assert "Python heap per file" in run(str(example_pdf), "--memory")
    assert "Top functions by cumulative time" in run(str(example_pdf), "--profile")


def test_benchmark_without_pdfs_fails(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "no PDF files found" in result.stderr
