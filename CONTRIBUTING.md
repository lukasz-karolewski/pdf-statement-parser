# Contributing

Thanks for helping. This project deals with private financial documents, so the
privacy rules matter more than usual.

## Development setup

```bash
git clone https://github.com/lukasz-karolewski/pdf-statement-parser.git
cd pdf-statement-parser
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

Run checks before opening a pull request:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check src tests scripts examples
.venv/bin/mypy
.venv/bin/python -m pytest tests --ignore=tests/parsers -q
```

Build locally with:

```bash
uv build
```

## Validating private statements

You can validate your own PDFs without committing them:

```bash
statement-parser validate statements/ -j 8 --report corpus-report.json
```

Keep `statements/` local. Do not attach real statements to issues or pull
requests.

If a change touches text extraction or a hot parser path, compare timings
before and after:

```bash
python scripts/benchmark.py statements/
```

See [docs/performance.md](docs/performance.md).

## Fixtures and privacy

Tests must use synthetic documents. Do not commit real bank statements, cropped
statement images, real dump output, or screenshots. If you need to discuss a
format, run:

```bash
statement-parser dump your-statement.pdf --page 1
```

Then replace names, addresses, account numbers, and amounts before sharing the
text.

## Parser changes

Use `Document.from_text` for focused parser tests. Use the helpers in
`pdf_statement_parser.utils` for dates, amounts, and whitespace. Keep the sign
convention consistent: positive means money in, negative means money out.

## Commits and pull requests

Keep pull requests focused. Explain which statement type changed and how you
validated it. Add or update docs when user-visible behavior changes.
