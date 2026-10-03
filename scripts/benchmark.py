"""Benchmark parsing speed over a private corpus of statement PDFs.

Each file is timed in phases: PDF open, text extraction (all pages), parser
detection, and parsing. Real statements are private and never committed:

    python scripts/benchmark.py statements/
    python scripts/benchmark.py statements/ -j 8
    python scripts/benchmark.py statements/ --limit 50 --profile
    python scripts/benchmark.py statements/ --memory

``--memory`` traces Python allocations with tracemalloc, which slows parsing
down, so measure time and memory in separate runs.
"""

from __future__ import annotations

import argparse
import cProfile
import functools
import gc
import json
import pstats
import statistics
import sys
import time
import tracemalloc
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from pdf_statement_parser.document import Document
from pdf_statement_parser.registry import default_registry

PHASES = ("open", "extract", "detect", "parse")


def collect(paths: Sequence[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.suffix.lower() == ".pdf"))
        else:
            files.append(path)
    return files


def max_rss_mib() -> float | None:
    try:
        import resource
    except ImportError:  # Windows
        return None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024 if sys.platform == "darwin" else 1024)


def bench_file(path: Path, memory: bool = False) -> dict[str, Any]:
    registry = default_registry()
    row: dict[str, Any] = {"file": str(path), "bytes": path.stat().st_size}
    if memory:
        gc.collect()
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
    t0 = time.perf_counter()
    doc = Document.open(path)
    try:
        t1 = time.perf_counter()
        _ = doc.text
        t2 = time.perf_counter()
        detection = registry.detect(doc)
        t3 = time.perf_counter()
        statements = []
        error = None
        if detection is not None:
            try:
                statements = detection.parser.parse(doc)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        t4 = time.perf_counter()
        row.update(
            pages=doc.page_count,
            parser=detection.parser_id if detection else None,
            transactions=sum(len(s.transactions) for s in statements),
            error=error,
            open=t1 - t0,
            extract=t2 - t1,
            detect=t3 - t2,
            parse=t4 - t3,
        )
    finally:
        doc.close()
    row["total"] = time.perf_counter() - t0
    if memory:
        row["mem_peak"] = tracemalloc.get_traced_memory()[1] - base
        gc.collect()
        row["mem_retained"] = tracemalloc.get_traced_memory()[0] - base
    return row


def pct(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def ms(seconds: float) -> str:
    return f"{seconds * 1000:8.1f}"


def summarize(rows: list[dict[str, Any]], wall: float, jobs: int, top: int) -> None:
    totals = [r["total"] for r in rows]
    pages = sum(r["pages"] for r in rows)
    txns = sum(r["transactions"] for r in rows)
    cpu = sum(totals)
    print(f"\nFiles: {len(rows)}   pages: {pages}   transactions: {txns}   jobs: {jobs}")
    print(f"Wall time: {wall:.2f}s   summed per-file time: {cpu:.2f}s")
    print(
        f"Throughput: {len(rows) / wall:.1f} files/s   {pages / wall:.1f} pages/s   "
        f"{txns / wall:.0f} transactions/s"
    )
    print(f"Per page (single-process time): {cpu / pages * 1000:.1f} ms")

    print("\nPer-file latency (ms):")
    print(f"  {'phase':<8}{'mean':>9}{'median':>9}{'p95':>9}{'max':>9}{'share':>8}")
    for phase in (*PHASES, "total"):
        vals = [r[phase] for r in rows]
        share = sum(vals) / cpu * 100
        print(
            f"  {phase:<8}{ms(statistics.mean(vals))}{ms(statistics.median(vals))}"
            f"{ms(pct(vals, 0.95))}{ms(max(vals))}{share:7.1f}%"
        )

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[r["parser"] or "<undetected>"].append(r)
    print("\nBy parser:")
    print(f"  {'parser':<22}{'files':>6}{'pages':>7}{'ms/file':>9}{'ms/page':>9}{'parse ms':>9}")
    for name, items in sorted(groups.items()):
        n_pages = sum(r["pages"] for r in items)
        total = sum(r["total"] for r in items)
        parse = sum(r["parse"] for r in items)
        print(
            f"  {name:<22}{len(items):>6}{n_pages:>7}{ms(total / len(items)):>9}"
            f"{ms(total / n_pages):>9}{ms(parse / len(items)):>9}"
        )

    print(f"\nSlowest {top} files:")
    for r in sorted(rows, key=lambda r: r["total"], reverse=True)[:top]:
        print(f"  {ms(r['total'])} ms  {r['pages']:>3}p  {r['parser'] or '-':<20} {r['file']}")

    if "mem_peak" in rows[0]:
        mib = 1024 * 1024
        peaks = [r["mem_peak"] / mib for r in rows]
        per_page = [r["mem_peak"] / r["pages"] / mib for r in rows if r["pages"]]
        retained = [r["mem_retained"] / mib for r in rows]
        print("\nPython heap per file (tracemalloc, MiB):")
        print(f"  {'metric':<14}{'mean':>9}{'median':>9}{'p95':>9}{'max':>9}")
        for label, vals in (("peak", peaks), ("peak / page", per_page), ("retained", retained)):
            print(
                f"  {label:<14}{statistics.mean(vals):9.2f}{statistics.median(vals):9.2f}"
                f"{pct(vals, 0.95):9.2f}{max(vals):9.2f}"
            )
        biggest = max(rows, key=lambda r: r["mem_peak"])
        print(
            f"  largest peak: {biggest['mem_peak'] / mib:.1f} MiB, {biggest['pages']}p, "
            f"{biggest['file']}"
        )

    errors = [r for r in rows if r["error"] or r["parser"] is None]
    if errors:
        print(f"\nUndetected or failed: {len(errors)}")
        for r in errors[:top]:
            print(f"  {r['file']}: {r['error'] or 'no parser detected'}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", metavar="FILE_OR_DIR")
    ap.add_argument("-j", "--jobs", type=int, default=1)
    ap.add_argument("--limit", type=int, help="only benchmark the first N files")
    ap.add_argument("--warmup", type=int, default=1, help="files to parse before timing")
    ap.add_argument("--top", type=int, default=10, help="slowest files to list")
    ap.add_argument("--profile", action="store_true", help="cProfile a single-process run")
    ap.add_argument("--memory", action="store_true", help="trace per-file Python heap usage")
    ap.add_argument("--json", type=Path, help="write per-file timings to this file")
    args = ap.parse_args(argv)

    files = collect(args.paths)[: args.limit]
    run = functools.partial(bench_file, memory=args.memory)
    rss_start = max_rss_mib()
    if not files:
        print("no PDF files found", file=sys.stderr)
        return 1
    for path in files[: args.warmup]:
        run(path)

    profiler = cProfile.Profile() if args.profile else None
    start = time.perf_counter()
    if args.jobs > 1 and profiler is None:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            rows = list(pool.map(run, files, chunksize=4))
    else:
        if profiler:
            profiler.enable()
        rows = [run(p) for p in files]
        if profiler:
            profiler.disable()
    wall = time.perf_counter() - start

    summarize(rows, wall, 1 if profiler else args.jobs, args.top)
    rss_end = max_rss_mib()
    if rss_start is not None and rss_end is not None and args.jobs == 1:
        print(
            f"\nProcess max RSS: {rss_start:.0f} MiB after import and warmup, "
            f"{rss_end:.0f} MiB at end"
        )
    if profiler:
        print("\nTop functions by cumulative time:")
        pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)
        print("Top functions by own time:")
        pstats.Stats(profiler).sort_stats("tottime").print_stats(15)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))
        print(f"wrote {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
