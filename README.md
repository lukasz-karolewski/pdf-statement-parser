# pdf-statement-parser

`statement-parser` extracts account metadata and transactions from bank statement PDFs.
Each statement format is a parser plugin. The registry runs detection and picks the
best parser for a document.

Do not upload real statements to issues, pull requests, or fixtures. Use
`statement-parser dump` on your own machine and redact names, addresses, account
numbers, and amounts before sharing text.

## Install

```bash
pip install pdf-statement-parser
```

For local development:

```bash
uv pip install --python .venv/bin/python -e '.[dev]'
```

## CLI quickstart

Parse one file as JSON:

```bash
statement-parser parse examples/fake-example-bank.pdf
```

Example output with fake data:

```json
[
  {
    "source": "examples/fake-example-bank.pdf",
    "parser_id": "example-bank",
    "bank": "Example Bank",
    "page_count": 1,
    "statements": [
      {
        "bank": "Example Bank",
        "account_type": "checking",
        "account_number": "00001234",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "account_name": "Example Checking",
        "account_holder": "Jane Doe",
        "currency": "USD",
        "opening_balance": "100.00",
        "closing_balance": "130.00",
        "total_deposits": "50.00",
        "total_expenses": "20.00",
        "transactions": [
          {
            "date": "2026-01-05",
            "description": "Coffee",
            "amount": "-20.00",
            "balance": "80.00",
            "kind": "purchase",
            "post_date": null,
            "extra": {}
          }
        ],
        "extra": {},
        "warnings": [],
        "account_last4": "1234"
      }
    ]
  }
]
```

Useful commands:

```bash
statement-parser parse statements/ --format csv -o statements.csv -r -j 8
statement-parser transactions statements/ --format csv -o transactions.csv
statement-parser rewards statements/ --format table
statement-parser detect statements/example.pdf --all
statement-parser validate statements/ -j 8 --report report.json
statement-parser dump statements/example.pdf --page 1
statement-parser parsers
python -m pdf_statement_parser parse statements/example.pdf
```

Fake rewards output:

```text
source                         account_last4  account_name   period_start  period_end    program          unit    opening_balance  closing_balance  earned  welcome_bonus  anniversary_bonus  other_bonus  adjustments  transferred  redeemed  year_to_date  difference
-----------------------------  -------------  -------------  ------------  ------------  ---------------  ------  ---------------  ---------------  ------  -------------  -----------------  -----------  -----------  -----------  --------  ------------  ----------
examples/fake-example-bank.pdf 1234           Example Card   2026-01-01    2026-01-31    Example Rewards  points  1000             1450             500                                                          -50                       0
```

Directories expand to `*.pdf` in that directory. `parse -r` searches directories
recursively. `validate` always searches directories recursively because corpus
validation usually works on nested folders.

`parse`, `transactions`, `rewards` and `validate` take `-j N` to parse files on
N processes. Output keeps input order.

## Performance

A typical Chase statement parses in about 0.2 seconds on one core, roughly
50 ms per page. Numbers below come from 585 real Chase statements (397 credit
card, 188 checking or savings, 2,346 pages) on an Intel i9-11900KF with
8 cores, Python 3.14 and pdfplumber 0.11.10.

| One process | 0.1.0a2 | 0.1.0b1 |
| --- | ---: | ---: |
| Whole corpus | 983 s | 118 s |
| Mean per file | 1,681 ms | 201 ms |
| p95 per file | 4,518 ms | 417 ms |
| Slowest file | 9,646 ms | 727 ms |
| Per page | 419 ms | 50 ms |

About 96% of the time goes to pdfminer reading the PDF and pdfplumber building
character objects. Detection takes under 0.1 ms and parser code about 5 ms per
file. Version 0.1.0b1 returns byte-identical text and results to 0.1.0a2 on
the whole corpus.

Files are independent, so `-j` helps up to the number of physical cores:

| Workers | 1 | 2 | 4 | 8 | 16 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Corpus wall time | 118 s | 59 s | 36 s | 26 s | 23 s |
| Files per second | 5.0 | 10.0 | 16.2 | 22.2 | 25.5 |

Peak Python heap is about 20 MiB per file, or 5 MiB per page. The largest
statement, 10 pages, peaked at 70 MiB. Nothing stays allocated after a document
closes.

Measure your own statements with `scripts/benchmark.py`. It times each phase
per file (open, extract, detect, parse) and prints a summary by parser and the
slowest files. Real statements stay on your machine.

```bash
python scripts/benchmark.py statements/                   # one process
python scripts/benchmark.py statements/ -j 8              # throughput
python scripts/benchmark.py statements/ --memory          # tracemalloc peaks
python scripts/benchmark.py statements/ --limit 50 --profile
python scripts/benchmark.py statements/ --json timings.json
```

Run timing and `--memory` separately, because tracemalloc slows parsing down.
[docs/performance.md](docs/performance.md) explains the 0.1.0b1 speedups and
the options left out.

## Python API quickstart

```python
from pdf_statement_parser import parse

result = parse("statement.pdf")
statement = result.statement
print(statement.bank, statement.account_last4, statement.closing_balance)
for transaction in statement.transactions:
    print(transaction.date, transaction.description, transaction.amount)
```

Use a specific parser when you already know the format:

```python
result = parse("statement.pdf", parser="chase-credit-card")
```

## Data model

| Field | Meaning |
| --- | --- |
| `ParseResult` | One parsed PDF, including parser id, bank, source, page count, and statements. |
| `Statement` | One account in one statement period. Consolidated PDFs can return several statements. |
| `Transaction` | One transaction with date, description, signed amount, optional running balance, and kind. |
| `Transaction.amount` | Signed from the account holder's point of view. Positive means money in. Negative means money out. |
| `total_deposits` | Non-negative total for money in during the period. |
| `total_expenses` | Non-negative total for money out during the period. |
| `opening_balance`, `closing_balance` | Balances as printed by the bank. For credit cards, a positive balance is the amount owed. |
| `rewards` | Optional points or miles summary for the period, or `None`. See below. |

JSON writes `Decimal` values as strings. CSV writes amounts as plain numeric text.

Rewards movement fields are signed by their effect on the rewards balance.
Points or miles that arrive are positive. Redemptions and transfers are
negative. `None` means the statement did not print that value. Per-category earn
lines are added into `earned` and are not exported separately.

| Rewards field | Meaning |
| --- | --- |
| `program`, `unit` | Program name and unit, such as points or miles. |
| `opening_balance`, `closing_balance` | Printed rewards balances when present. |
| `earned` | Sum of base and category earnings for the period. |
| `welcome_bonus`, `anniversary_bonus`, `other_bonus` | Printed bonus movements. |
| `adjustments`, `transferred`, `redeemed` | Other signed movements. |
| `year_to_date` | Printed year-to-date total. It is not part of period arithmetic. |
| `difference` | `(closing - opening) - movements`. `0` means the rewards summary reconciles. |

## Supported statements

| Bank | Statement type | Status |
| --- | --- | --- |
| Chase | Credit cards, including rewards | Supported. See [docs/providers/chase.md](docs/providers/chase.md). |
| Chase | Checking and savings | Supported. See [docs/providers/chase.md](docs/providers/chase.md). |
| Chase | Consolidated personal statements | Supported. See [docs/providers/chase.md](docs/providers/chase.md). |
| Chase | Business checking | Supported. See [docs/providers/chase.md](docs/providers/chase.md). |

## Detection

Each parser implements `detect(document) -> float`. The registry runs every
parser, clamps scores to 0.0 through 1.0, ignores parsers that raise during
detection, and picks the highest score at or above the detection threshold. If
scores tie, the parser with the higher `priority` wins.

## Writing a parser

Start with `statement-parser dump your-statement.pdf`. Redact the output before
sharing it. Follow `docs/writing-a-parser.md` for in-tree parsers and plugins.
The example plugin in `examples/example_bank_plugin/` shows the entry point
needed for out-of-tree packages.

## Privacy

Bank statements contain names, addresses, account numbers, balances, and
transactions. Keep real PDFs out of the repository. Redact dump output before
posting it. Synthetic PDFs are fine for tests when they contain only fake data.

## License

MIT. See `LICENSE`.
