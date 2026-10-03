"""Validate a private corpus of statement PDFs.

Real statements are private and never committed. Point this at your own files:

    python scripts/validate_corpus.py statements/ -j 8 --report corpus-report.json
"""

from __future__ import annotations

import sys

from pdf_statement_parser.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["validate", *sys.argv[1:]]))
