"""Command-line interface for statement-parser."""

from __future__ import annotations

import argparse
import csv
import functools
import json
import logging
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

from pdf_statement_parser.__about__ import __version__
from pdf_statement_parser.api import detect as api_detect
from pdf_statement_parser.api import parse as api_parse
from pdf_statement_parser.document import Document
from pdf_statement_parser.exceptions import UnsupportedStatementError
from pdf_statement_parser.models import ParseResult, Statement
from pdf_statement_parser.registry import default_registry
from pdf_statement_parser.validation import Issue, not_printed, reconcile

log = logging.getLogger(__name__)

REWARDS_FIELDS = (
    "program",
    "unit",
    "opening_balance",
    "closing_balance",
    "earned",
    "welcome_bonus",
    "anniversary_bonus",
    "other_bonus",
    "adjustments",
    "transferred",
    "redeemed",
    "year_to_date",
    "difference",
)

REWARDS_COLUMNS = (
    "source",
    "account_last4",
    "account_name",
    "period_start",
    "period_end",
    *REWARDS_FIELDS,
)

REQUIRED_VALIDATION_FIELDS = (
    "account_number",
    "account_name",
    "account_holder",
    "period_start",
    "period_end",
    "opening_balance",
    "closing_balance",
    "total_deposits",
    "total_expenses",
)


class CliError(Exception):
    """Raised for command-line errors that should print as one line."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="statement-parser",
        description="Extract metadata and transactions from bank statement PDFs.",
    )
    parser.add_argument("--version", action="version", version=f"statement-parser {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="increase logging")
    sub = parser.add_subparsers(dest="command")

    parse_cmd = sub.add_parser("parse", help="parse statements")
    parse_cmd.add_argument("paths", nargs="+", metavar="FILE_OR_DIR")
    parse_cmd.add_argument("--format", choices=("json", "csv", "table"), default="json")
    parse_cmd.add_argument("-o", "--out", type=Path)
    parse_cmd.add_argument("--parser", dest="parser_id")
    parse_cmd.add_argument("--no-transactions", action="store_true")
    parse_cmd.add_argument("--password")
    parse_cmd.add_argument("-r", "--recursive", action="store_true")
    parse_cmd.add_argument("-j", "--jobs", type=int, default=1, help="parallel worker processes")
    parse_cmd.set_defaults(func=cmd_parse)

    tx_cmd = sub.add_parser("transactions", help="write flat transaction rows")
    tx_cmd.add_argument("paths", nargs="+", metavar="FILE_OR_DIR")
    tx_cmd.add_argument("--format", choices=("csv", "json"), default="csv")
    tx_cmd.add_argument("-o", "--out", type=Path)
    tx_cmd.add_argument("-j", "--jobs", type=int, default=1, help="parallel worker processes")
    tx_cmd.set_defaults(func=cmd_transactions)

    rewards_cmd = sub.add_parser("rewards", help="write rewards rows")
    rewards_cmd.add_argument("paths", nargs="+", metavar="FILE_OR_DIR")
    rewards_cmd.add_argument("--format", choices=("table", "csv", "json"), default="table")
    rewards_cmd.add_argument("-o", "--out", type=Path)
    rewards_cmd.add_argument("-j", "--jobs", type=int, default=1, help="parallel worker processes")
    rewards_cmd.set_defaults(func=cmd_rewards)

    detect_cmd = sub.add_parser("detect", help="detect the best parser for each file")
    detect_cmd.add_argument("paths", nargs="+", metavar="FILE_OR_DIR")
    detect_cmd.add_argument("--all", action="store_true", help="show every parser score")
    detect_cmd.set_defaults(func=cmd_detect)

    validate_cmd = sub.add_parser("validate", help="parse and reconcile statements")
    validate_cmd.add_argument("paths", nargs="+", metavar="FILE_OR_DIR")
    validate_cmd.add_argument("-j", "--jobs", type=int, default=1)
    validate_cmd.add_argument("--report", type=Path)
    validate_cmd.add_argument(
        "--strict-rewards",
        action="store_true",
        help="fail validation when printed rewards movements do not reconcile",
    )
    validate_cmd.set_defaults(func=cmd_validate)

    parsers_cmd = sub.add_parser("parsers", help="list registered parsers")
    parsers_cmd.set_defaults(func=cmd_parsers)

    dump_cmd = sub.add_parser("dump", help="print extracted page text")
    dump_cmd.add_argument("file", type=Path, metavar="FILE")
    dump_cmd.add_argument("--page", type=int, help="1-based page number")
    dump_cmd.set_defaults(func=cmd_dump)

    return parser


def cmd_parse(args: argparse.Namespace) -> int:
    paths, missing = expand_inputs(args.paths, recursive=args.recursive)
    errors = _missing_errors(missing)
    results = _parse_many(
        paths, errors, jobs=args.jobs, parser_id=args.parser_id, password=args.password
    )
    include_transactions = not bool(args.no_transactions)
    if args.format == "json":
        output = json.dumps(
            [r.to_dict(include_transactions=include_transactions) for r in results],
            indent=2,
        )
    elif args.format == "csv":
        output = _parse_csv(results)
    else:
        output = _parse_table(results, include_transactions=include_transactions)
    _write_output(args.out, output)
    return _finish_batch(errors)


def cmd_transactions(args: argparse.Namespace) -> int:
    paths, missing = expand_inputs(args.paths, recursive=False)
    rows: list[dict[str, object]] = []
    errors = _missing_errors(missing)
    for result in _parse_many(paths, errors, jobs=args.jobs):
        rows.extend(_transaction_rows(result))
    if args.format == "json":
        output = json.dumps(rows, indent=2, default=_json_default)
    else:
        output = _rows_csv(
            rows,
            [
                "source",
                "statement_index",
                "account_last4",
                "account_name",
                "account_type",
                "date",
                "post_date",
                "description",
                "amount",
                "balance",
                "kind",
            ],
        )
    _write_output(args.out, output)
    return _finish_batch(errors)


def cmd_rewards(args: argparse.Namespace) -> int:
    paths, missing = expand_inputs(args.paths, recursive=False)
    errors = _missing_errors(missing)
    rows = _rewards_rows(_parse_many(paths, errors, jobs=args.jobs))
    if args.format == "json":
        output = json.dumps(rows, indent=2, default=_json_default)
    elif args.format == "csv":
        output = _rows_csv(rows, REWARDS_COLUMNS)
    else:
        output = _simple_table(rows, REWARDS_COLUMNS) if rows else ""
    _write_output(args.out, output)
    return _finish_batch(errors)


def cmd_detect(args: argparse.Namespace) -> int:
    registry = default_registry()
    paths, missing = expand_inputs(args.paths, recursive=False)
    errors = _missing_errors(missing)
    lines: list[str] = []
    for path in paths:
        try:
            with Document.open(path) as doc:
                if args.all:
                    ranked = registry.rank(doc)
                    best = registry.detect(doc)
                    label = (
                        f"{best.parser.id} {best.confidence:.2f}"
                        if best is not None
                        else "unrecognised"
                    )
                    lines.append(f"{path}: {label}")
                    for ranked_detection in ranked:
                        lines.append(
                            f"  {ranked_detection.parser.id}: {ranked_detection.confidence:.2f}"
                        )
                else:
                    detection = api_detect(doc, registry=registry)
                    if detection is None:
                        lines.append(f"{path}: unrecognised")
                    else:
                        lines.append(f"{path}: {detection.parser.id} {detection.confidence:.2f}")
        except Exception as exc:
            errors.append(f"{path}: {_one_line_error(exc)}")
    _write_output(None, "\n".join(lines))
    return _finish_batch(errors)


def cmd_validate(args: argparse.Namespace) -> int:
    if args.jobs < 1:
        raise CliError("--jobs must be at least 1")
    paths, missing = expand_inputs(args.paths, recursive=True)
    rows = validate_files(paths, jobs=args.jobs)
    for path in missing:
        rows.append({"file": str(path), "status": "error", "error": "path does not exist"})
    for row in rows:
        if row["status"] == "ok":
            print(f"ok {row['file']}")
        else:
            print(f"FAIL {row['file']}: {_validation_detail(row)}")
    _print_validation_summary(rows)
    rewards_nonzero = _print_rewards_validation_summary(rows)
    if args.report:
        args.report.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    ok = bool(rows) and all(row["status"] == "ok" for row in rows)
    if args.strict_rewards and rewards_nonzero:
        ok = False
    return 0 if ok else 1


def cmd_parsers(args: argparse.Namespace) -> int:
    del args
    rows: list[dict[str, object]] = [
        {"id": parser.id, "bank": parser.bank, "description": parser.description}
        for parser in default_registry()
    ]
    if not rows:
        return 0
    print(_simple_table(rows, ["id", "bank", "description"]))
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    if not args.file.exists():
        raise CliError(f"{args.file}: path does not exist")
    try:
        with Document.open(args.file) as doc:
            if args.page is not None:
                if args.page < 1 or args.page > doc.page_count:
                    raise CliError(f"page must be between 1 and {doc.page_count}")
                print(doc.pages[args.page - 1].text)
                return 0
            parts: list[str] = []
            for page in doc.pages:
                if parts:
                    parts.append("\n")
                parts.append(f"--- page {page.number} ---\n")
                parts.append(page.text)
            print("".join(parts))
            return 0
    except CliError:
        raise
    except Exception as exc:
        raise CliError(f"{args.file}: {_one_line_error(exc)}") from exc


def expand_inputs(paths: Iterable[str | Path], *, recursive: bool) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    missing: list[Path] = []
    for value in paths:
        path = Path(value)
        if not path.exists():
            missing.append(path)
        elif path.is_dir():
            pattern = "**/*.pdf" if recursive else "*.pdf"
            files.extend(sorted(p for p in path.glob(pattern) if p.is_file()))
        else:
            files.append(path)
    return files, missing


def _parse_many(
    paths: Sequence[Path],
    errors: list[str],
    *,
    jobs: int = 1,
    parser_id: str | None = None,
    password: str | None = None,
) -> list[ParseResult]:
    """Parse ``paths`` in order on ``jobs`` processes, appending failures to ``errors``."""
    if jobs < 1:
        raise CliError("--jobs must be at least 1")
    work = functools.partial(_parse_one, parser_id=parser_id, password=password)
    names = [str(path) for path in paths]
    if jobs == 1 or len(names) < 2:
        outcomes = [work(name) for name in names]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(names))) as pool:
            outcomes = list(pool.map(work, names, chunksize=2))
    results: list[ParseResult] = []
    for name, outcome in zip(names, outcomes, strict=True):
        if isinstance(outcome, str):
            errors.append(f"{name}: {outcome}")
        else:
            results.append(outcome)
    return results


def _parse_one(path: str, parser_id: str | None, password: str | None) -> ParseResult | str:
    try:
        return api_parse(path, parser=parser_id, registry=default_registry(), password=password)
    except Exception as exc:
        return _one_line_error(exc)


def validate_files(paths: Sequence[Path], *, jobs: int = 1) -> list[dict[str, Any]]:
    """Parse and reconcile files, returning JSON-serialisable rows."""
    if jobs == 1:
        return [_validate_one(str(path)) for path in paths]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(_validate_one, [str(path) for path in paths], chunksize=4))


def _validate_one(path: str) -> dict[str, Any]:
    row: dict[str, Any] = {"file": path}
    try:
        result = api_parse(path)
    except UnsupportedStatementError as exc:
        return {**row, "status": "undetected", "error": str(exc)}
    except Exception as exc:
        return {**row, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    row["parser"] = result.parser_id
    accounts: list[dict[str, Any]] = []
    ok = bool(result.statements)
    for statement in result.statements:
        issues = reconcile(statement)
        absent = not_printed(statement)
        missing = [
            name
            for name in REQUIRED_VALIDATION_FIELDS
            if getattr(statement, name) is None and name not in absent
        ]
        ok = ok and not issues and not missing
        accounts.append(
            {
                "account_last4": statement.account_last4,
                "account_type": statement.account_type.value,
                "account_name": statement.account_name,
                "period": [
                    statement.period_start.isoformat() if statement.period_start else None,
                    statement.period_end.isoformat() if statement.period_end else None,
                ],
                "transactions": len(statement.transactions),
                "missing": missing,
                "issues": [_issue_text(issue) for issue in issues],
                "warnings": statement.warnings,
                "rewards": _rewards_validation_row(statement),
            }
        )
    row["accounts"] = accounts
    row["status"] = "ok" if ok else "mismatch"
    return row


def _parse_csv(results: Sequence[ParseResult]) -> str:
    rows: list[dict[str, object]] = []
    for result in results:
        for index, statement in enumerate(result.statements, start=1):
            rows.append(
                {
                    "source": result.source,
                    "parser_id": result.parser_id,
                    "bank": result.bank,
                    "page_count": result.page_count,
                    "statement_index": index,
                    "account_type": statement.account_type.value,
                    "account_number": statement.account_number,
                    "account_last4": statement.account_last4,
                    "account_name": statement.account_name,
                    "account_holder": statement.account_holder,
                    "period_start": statement.period_start,
                    "period_end": statement.period_end,
                    "currency": statement.currency,
                    "opening_balance": statement.opening_balance,
                    "closing_balance": statement.closing_balance,
                    "total_deposits": statement.total_deposits,
                    "total_expenses": statement.total_expenses,
                    "transaction_count": len(statement.transactions),
                    "warnings": "; ".join(statement.warnings),
                    **_prefixed_rewards(statement),
                }
            )
    return _rows_csv(
        rows,
        [
            "source",
            "parser_id",
            "bank",
            "page_count",
            "statement_index",
            "account_type",
            "account_number",
            "account_last4",
            "account_name",
            "account_holder",
            "period_start",
            "period_end",
            "currency",
            "opening_balance",
            "closing_balance",
            "total_deposits",
            "total_expenses",
            "transaction_count",
            "warnings",
            "rewards_program",
            "rewards_unit",
            "rewards_opening_balance",
            "rewards_closing_balance",
            "rewards_earned",
            "rewards_welcome_bonus",
            "rewards_anniversary_bonus",
            "rewards_other_bonus",
            "rewards_adjustments",
            "rewards_transferred",
            "rewards_redeemed",
            "rewards_year_to_date",
            "rewards_difference",
        ],
    )


def _rewards_rows(results: Sequence[ParseResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        for statement in result.statements:
            if statement.rewards is not None:
                rows.append(_rewards_row(result.source, statement))
    return rows


def _rewards_row(source: str | None, statement: Statement) -> dict[str, object]:
    row: dict[str, object] = {
        "source": source,
        "account_last4": statement.account_last4,
        "account_name": statement.account_name,
        "period_start": statement.period_start,
        "period_end": statement.period_end,
    }
    rewards = statement.rewards
    for field in REWARDS_FIELDS:
        row[field] = getattr(rewards, field) if rewards is not None else None
    return row


def _prefixed_rewards(statement: Statement) -> dict[str, object]:
    rewards = statement.rewards
    return {
        f"rewards_{field}": getattr(rewards, field) if rewards is not None else None
        for field in REWARDS_FIELDS
    }


def _rewards_validation_row(statement: Statement) -> dict[str, object] | None:
    if statement.rewards is None:
        return None
    return {
        "program": statement.rewards.program,
        "unit": statement.rewards.unit,
        "difference": statement.rewards.difference,
    }


def _transaction_rows(result: ParseResult) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, statement in enumerate(result.statements, start=1):
        for transaction in statement.transactions:
            rows.append(
                {
                    "source": result.source,
                    "statement_index": index,
                    "account_last4": statement.account_last4,
                    "account_name": statement.account_name,
                    "account_type": statement.account_type.value,
                    "date": transaction.date,
                    "post_date": transaction.post_date,
                    "description": transaction.description,
                    "amount": transaction.amount,
                    "balance": transaction.balance,
                    "kind": transaction.kind,
                }
            )
    return rows


def _parse_table(results: Sequence[ParseResult], *, include_transactions: bool) -> str:
    parts: list[str] = []
    for result in results:
        parts.append(f"{result.source or '<memory>'} [{result.parser_id}]")
        for index, statement in enumerate(result.statements, start=1):
            period = _format_period(statement)
            parts.append(
                f"  {index}. {statement.bank} {statement.account_name or ''} "
                f"{statement.account_last4 or ''} {period}".rstrip()
            )
            parts.append(
                "     "
                f"opening={_cell(statement.opening_balance)} "
                f"closing={_cell(statement.closing_balance)} "
                f"deposits={_cell(statement.total_deposits)} "
                f"expenses={_cell(statement.total_expenses)} "
                f"transactions={len(statement.transactions)}"
            )
            if statement.rewards is not None:
                rewards = statement.rewards
                parts.append(
                    "     "
                    f"rewards {rewards.program or ''} {rewards.unit}: "
                    f"opening={_cell(rewards.opening_balance)} "
                    f"closing={_cell(rewards.closing_balance)} "
                    f"earned={_cell(rewards.earned)} "
                    f"redeemed={_cell(rewards.redeemed)} "
                    f"difference={_cell(rewards.difference)}"
                )
            if include_transactions and statement.transactions:
                rows: list[dict[str, object]] = [
                    {
                        "date": tx.date,
                        "description": tx.description,
                        "amount": tx.amount,
                        "balance": tx.balance,
                        "kind": tx.kind,
                    }
                    for tx in statement.transactions
                ]
                parts.append(
                    _indent(
                        _simple_table(rows, ["date", "description", "amount", "balance", "kind"]), 5
                    )
                )
        parts.append("")
    return "\n".join(parts).rstrip()


def _rows_csv(rows: Sequence[dict[str, object]], fieldnames: Sequence[str]) -> str:
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(row.get(key)) for key in fieldnames})
    return buffer.getvalue().rstrip("\r\n")


def _simple_table(rows: Sequence[dict[str, object]], columns: Sequence[str]) -> str:
    widths = {column: len(column) for column in columns}
    rendered: list[dict[str, str]] = []
    for row in rows:
        item = {column: _cell(row.get(column)) for column in columns}
        rendered.append(item)
        for column, value in item.items():
            widths[column] = max(widths[column], len(value))
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    sep = "  ".join("-" * widths[column] for column in columns)
    body = ["  ".join(row[column].ljust(widths[column]) for column in columns) for row in rendered]
    return "\n".join([header, sep, *body])


def _format_period(statement: Statement) -> str:
    start = statement.period_start.isoformat() if statement.period_start else "?"
    end = statement.period_end.isoformat() if statement.period_end else "?"
    return f"{start} to {end}"


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _write_output(path: Path | None, text: str) -> None:
    if path is None:
        if text:
            print(text)
        return
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def _finish_batch(errors: Sequence[str]) -> int:
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} file(s) failed", file=sys.stderr)
        return 1
    return 0


def _missing_errors(paths: Sequence[Path]) -> list[str]:
    return [f"{path}: path does not exist" for path in paths]


def _one_line_error(exc: BaseException) -> str:
    return " ".join(f"{type(exc).__name__}: {exc}".split())


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


def _issue_text(issue: Issue) -> str:
    return f"{issue.check}: {issue.message}"


def _validation_detail(row: dict[str, Any]) -> str:
    if "error" in row:
        return str(row["error"])
    parts: list[str] = []
    for account in row.get("accounts", []):
        details = list(account.get("issues", []))
        details.extend(f"missing {name}" for name in account.get("missing", []))
        if details:
            last4 = account.get("account_last4") or "unknown"
            parts.append(f"[{last4}] " + "; ".join(details))
    return "; ".join(parts) or str(row["status"])


def _print_validation_summary(rows: Sequence[dict[str, Any]]) -> None:
    status = Counter(str(row["status"]) for row in rows)
    total = sum(status.values())
    print(f"summary: {total} files, " + ", ".join(f"{k}={v}" for k, v in sorted(status.items())))
    issue_kinds: Counter[str] = Counter()
    for row in rows:
        for account in row.get("accounts", []):
            issue_kinds.update(str(issue).split(":", 1)[0] for issue in account.get("issues", []))
            issue_kinds.update(f"missing:{name}" for name in account.get("missing", []))
    for issue, count in issue_kinds.most_common():
        print(f"  {issue}: {count}")


def _print_rewards_validation_summary(rows: Sequence[dict[str, Any]]) -> int:
    count = 0
    zero = 0
    nonzero = 0
    unknown = 0
    notes: list[str] = []
    for row in rows:
        for account in row.get("accounts", []):
            rewards = account.get("rewards")
            if rewards is None:
                continue
            count += 1
            difference = rewards.get("difference")
            if difference is None:
                unknown += 1
            elif difference == 0:
                zero += 1
            else:
                nonzero += 1
                last4 = account.get("account_last4") or "unknown"
                notes.append(f"note {row['file']} [{last4}] rewards difference {difference}")
    print(f"rewards: statements={count}, zero={zero}, nonzero={nonzero}, none={unknown}")
    for note in notes:
        print(note)
    return nonzero


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def _configure_logging(verbosity: int) -> None:
    if verbosity <= 0:
        level = logging.WARNING
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


if __name__ == "__main__":
    raise SystemExit(main())
