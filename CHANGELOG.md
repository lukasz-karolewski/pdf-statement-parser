# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

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

### Known issues

- Chase credit card `total_deposits` and `total_expenses` are computed from the
  extracted transactions instead of the printed account summary.
