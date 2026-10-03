# Example bank plugin

This is a small out-of-tree parser for the synthetic text used in the main
project's documentation and tests. It is not for a real bank.

Install it next to `statement-parser`:

```bash
uv pip install -e examples/example_bank_plugin
statement-parser parsers
```

The package exposes an entry point in the `pdf_statement_parser.parsers` group.
