# Writing a parser

This guide shows the usual path for adding a statement format.

## Dump the text

Run the dump command on a private PDF:

```bash
statement-parser dump statements/example.pdf --page 1
```

The command prints `Document.pages[n].text`, which is the text parsers see. Do
not paste raw dump output into an issue or pull request. Replace names,
addresses, account numbers, and amounts first.

## Write detection

Create a `StatementParser` subclass. `detect()` should be cheap. Look at the
first page when you can and return:

- `0.0` when the document is not for this parser.
- A score at or above the threshold when it is a match.
- A higher score for stronger evidence, such as bank name plus account product.

The registry catches exceptions from `detect()`, logs them, and treats the score
as `0.0`.

## Write parsing

`parse()` returns a list of `Statement` objects. Most PDFs return one statement.
Consolidated PDFs can return more than one.

Set metadata first: bank, account type, account number, statement period,
opening and closing balances, account name, and account holder when the format
prints them. Then parse transactions.

## Use the sign convention

`Transaction.amount` is from the account holder's point of view. Positive means
money in. Negative means money out. This rule is the same for deposits and
credit cards.

`total_deposits` and `total_expenses` are non-negative summary totals. Balances
should match the way the statement prints them. For credit cards, a positive
balance is the amount owed.

## Parse rewards

Use `Rewards` when the statement prints a points or miles summary. Movement
fields are signed by their effect on the rewards balance. Points or miles that
arrive are positive. Redemptions and transfers are negative.

Use `None` when the statement does not print a value. Use `0` only when it
prints zero. Sum per-category earn lines into `earned`; do not export the
categories separately. Set `difference` to `(closing - opening) - movements`
when the parser has enough printed data to compute it. Leave it as `None` when
the program does not print balances or another required figure is missing.

## Use helpers

`pdf_statement_parser.utils` has helpers for common parser work:

- `parse_amount()` for printed money values.
- `parse_date()` for common full dates.
- `resolve_month_day()` for transaction dates that omit the year.
- `normalize_space()` for whitespace cleanup.

## Test with synthetic fixtures

Use `Document.from_text()` for small tests:

```python
from pdf_statement_parser.document import Document

text = "Example Bank Statement\nAccount: 00001234\n"
doc = Document.from_text(text, name="example.txt")
```

Do not commit real PDFs. If a PDF fixture is needed, generate it in the test
from fake text.

## Register the parser

For an in-tree parser, add the class path to `BUILTIN_PARSERS` in
`pdf_statement_parser.registry`.

For an out-of-tree package, expose an entry point:

```toml
[project.entry-points."pdf_statement_parser.parsers"]
example-bank = "example_bank_plugin.parser:ExampleBankParser"
```

See `examples/example_bank_plugin/` for a complete small package.

## Validate against your private corpus

After focused tests pass, run:

```bash
statement-parser validate statements/ -j 8 --report corpus-report.json
```

A file passes when detection succeeds, parsing succeeds, required metadata is
present, and `reconcile()` finds no balance or total mismatches.
