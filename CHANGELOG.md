# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

## 0.1.0b1 - 2026-10-03

First beta. Parsing is 8.4 times faster on one core, with identical output on
a 585-statement test corpus. See `docs/performance.md`.

### Added

- `-j/--jobs` for `parse`, `transactions` and `rewards` to parse files in
  parallel processes. Output keeps input order.
- `Page.deduped`: a cached pdfplumber page with duplicate glyphs removed,
  for parsers that need character positions.
- `document.dedupe_chars()` and `document.layout_chars()`, fast equivalents of
  pdfplumber's `Page.dedupe_chars()` and `Page.chars`.
- `scripts/benchmark.py` to time each parse phase and measure memory on a
  private corpus.

### Changed

- Removing duplicate bold glyphs no longer uses pdfplumber's
  `Page.dedupe_chars()`, which is quadratic in the number of characters. Dense
  card statements that took 6 to 10 seconds now take under 0.75 seconds.
- Char objects for text extraction are built without pdfplumber's generic
  attribute resolver, about 2.4 times faster. This needs pdfplumber 0.11.7 or
  later. Older releases fall back to `page.chars` automatically.
- The Chase card rewards parser reuses the deduped first page and crops the
  rewards box once.
- Development status is now Beta.

## 0.1.0a2 - 2026-10-03

### Added

- `Statement.rewards`: points and miles summary per statement with opening and
  closing balances, aggregate earnings, welcome, anniversary and other bonuses,
  signed adjustments, transfers and redemptions, reported year-to-date totals,
  and a reconciliation `difference`. Per-category earn lines are summed into
  `earned` and never exported.
- Chase rewards extraction for Ultimate Rewards, Prime Visa points,
  MileagePlus and Rapid Rewards. Balances the statement does not print stay
  `None`, and negative airline balances keep their sign.
- `statement-parser rewards` command, rewards columns in `parse --format csv`,
  a rewards block in `parse --format table`, and rewards notes in `validate`
  with `--strict-rewards` to fail on nonzero differences.

### Fixed

- The 0.1.0a1 notes wrongly said Chase card totals are computed from
  transactions. They come from the printed account summary.

## 0.1.0a1 - 2026-10-03

First public alpha. The Python API and CLI may still change before 0.1.0.

### Added

- Parser plugin system: each statement format is a `StatementParser` with
  `detect()` and `parse()`. Third-party parsers register through the
  `pdf_statement_parser.parsers` entry point group.
- Chase credit card parser (Freedom, Freedom Unlimited, Sapphire, Prime Visa,
  United, Southwest, Ink).
- Chase deposit parser (personal and business checking, savings, consolidated
  statements with several accounts).
- `reconcile()` checks that summary totals match the extracted transactions and
  that balances roll forward.
- CLI `statement-parser` with `parse`, `transactions`, `detect`, `validate`,
  `parsers` and `dump`.
