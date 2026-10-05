# Performance

Measured on 2026-10-03 against a private corpus of 585 Chase statements: 397
credit card and 188 checking or savings, 2,346 pages, 19,898 transactions.
Machine: Intel i9-11900KF (8 cores, 16 threads), Linux, Python 3.14.2,
pdfplumber 0.11.10, pdfminer.six 20260107.

Version 0.1.0b1 parses the corpus 8.4 times faster than 0.1.0a2 on one core and
returns byte-identical results. Every page's text and every parsed statement
matched before and after.

## Results

Single process, all 585 files. Repeat runs of 0.1.0b1 took 112 to 122 s, so
treat differences under 5% as noise.

| | 0.1.0a2 | 0.1.0b1 | Change |
| --- | ---: | ---: | ---: |
| Corpus wall time | 983 s | 118 s | 8.4x faster |
| Files per second | 0.6 | 5.0 | |
| Pages per second | 2.4 | 20.0 | |
| Mean per file | 1,681 ms | 201 ms | |
| Median per file | 1,083 ms | 164 ms | |
| p95 per file | 4,518 ms | 417 ms | |
| Slowest file | 9,646 ms | 727 ms | 13x faster |
| Credit card, per page | 498 ms | 54 ms | |
| Checking and savings, per page | 180 ms | 37 ms | |

The worst files before were 6 to 8 page card statements with dense pages. They
took 6 to 10 seconds each. The slowest file now is a 10-page statement at
0.7 seconds, and run time tracks page count.

### Where the time goes

Per-file phases in 0.1.0b1:

| Phase | Mean | Share |
| --- | ---: | ---: |
| Open the PDF | 2.0 ms | 1% |
| Extract text from every page | 194 ms | 96% |
| Detect the statement type | < 0.1 ms | 0% |
| Parse fields and transactions | 5.1 ms | 2.5% |

Inside text extraction, from a cProfile run over a sample of 15 files:

| Work | Share of parse time |
| --- | ---: |
| pdfminer interpreting page content streams | about 57% |
| Building pdfplumber char dicts | about 22% |
| Removing duplicate glyphs | about 10% |
| pdfplumber `extract_text` | about 8% |
| Parser code (regexes, decimals, models) | about 3% |

The library's own code is now a rounding error. What remains is reading the
PDF.

### Parallel runs

Files are independent, so the CLI can spread them across processes.
`parse`, `transactions`, `rewards` and `validate` take `-j N`. Corpus wall time
with `scripts/benchmark.py`:

| Workers | Wall time | Files per second |
| ---: | ---: | ---: |
| 1 | 118 s | 5.0 |
| 2 | 59 s | 10.0 |
| 4 | 36 s | 16.2 |
| 8 | 26 s | 22.2 |
| 16 | 23 s | 25.5 |

Scaling flattens past 4 workers on this 8-core machine, which also has turbo
clocks that drop under load. `-j` set to the number of physical cores is a good
default. `statement-parser parse statements/ -r -j 8` took 24 s against 129 s
with one worker and wrote the same JSON.

### Memory

Peak Python heap per file, measured with tracemalloc over every fifth file
(117 files):

| | 0.1.0a2 | 0.1.0b1 |
| --- | ---: | ---: |
| Mean peak | 21.2 MiB | 19.8 MiB |
| p95 peak | 39.3 MiB | 36.6 MiB |
| Largest peak (6 pages) | 45.8 MiB | 42.8 MiB |
| Peak per page | 5.4 MiB | 5.0 MiB |
| Retained after `close()` | 0.05 MiB | 0.05 MiB |

Memory grows with page count because pdfplumber keeps every page's objects
until the document closes. The largest file in the full corpus, 10 pages,
peaked at 70 MiB. Process RSS topped out near 210 MiB over a full run. Parsing
the same file twelve times in a row left live memory flat, so nothing leaks
between documents.

## What changed in 0.1.0b1

1. **Quadratic glyph dedupe.** Chase draws bold text twice with a small offset,
   so every page goes through pdfplumber's `Page.dedupe_chars()`. That method
   restores char order with `sorted(chars, key=chars.index)`. `list.index` is
   a linear scan that compares dicts, which makes the sort quadratic in the
   number of characters. A dense card page with a few thousand chars spent
   several seconds there. `pdf_statement_parser.document.dedupe_chars()` keeps
   pdfplumber's algorithm and drops the sort by filtering the original list.
2. **Repeated work on page 1.** The card rewards parser deduped page 1 four more
   times and cropped the rewards box twice. `Page.deduped` now caches the
   deduped page, and the parser crops the rewards box once.
3. **Cheaper char objects.** pdfplumber turns every layout object into a dict by
   running a generic recursive resolver over each attribute. Statements are
   98% chars, and char attributes are plain floats and strings.
   `layout_chars()` builds the same dicts for chars only, about 2.4 times
   faster. On every page it also builds the first char the pdfplumber way and
   compares. On a mismatch it switches to `page.chars` for the rest of the
   process. pdfplumber 0.11.0 to 0.11.6 build color fields differently, so they
   take that fallback and miss this speedup. They still get changes 1 and 2.
   The fallback also covers loaded page objects, Unicode normalization and
   layout analysis. Unit tests compare both paths.
4. **Parallel CLI.** `parse`, `transactions` and `rewards` gained `-j`, like
   `validate` already had. Output order follows input order.

Changes 1 and 2 made the parse phase about 70 times faster, from 333 ms to
5 ms per file, and cut text extraction from 1,346 ms to 231 ms. Change 3 took
extraction to 194 ms.

## Options not taken

- **A different PDF engine.** pypdfium2 or PyMuPDF would read pages several
  times faster, but they produce different text layout, so every parser would
  need retuning. PyMuPDF is also AGPL. Not worth it while parsers depend on
  pdfplumber's line layout.
- **Freeing pages after extraction.** Dropping each page's objects once its text
  is cached would hold peak memory to about one page. The card parser reads
  page 1 positions after detection, so it would interpret that page twice. On
  a typical 4-page card statement that is an estimated 20 to 25% slower.
  Memory is not a constraint at 5 MiB per page, so this stays off.
- **Skipping pages.** Detection already reads page 1 only. Parsers need every
  page for transactions.

## Reproduce

`scripts/benchmark.py` times each phase per file and prints a summary by parser
plus the slowest files. Real statements stay local.

```bash
python scripts/benchmark.py statements/                  # single process
python scripts/benchmark.py statements/ -j 8             # throughput
python scripts/benchmark.py statements/ --memory         # tracemalloc peaks
python scripts/benchmark.py statements/ --limit 50 --profile
python scripts/benchmark.py statements/ --json timings.json
```

Run timing and `--memory` separately. tracemalloc slows parsing down.
