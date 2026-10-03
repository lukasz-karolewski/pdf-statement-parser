"""Chase deposit account statements.

The parser covers Chase checking and savings statements, including older
consolidated "ASSETS" mailers and business checking statements.

Transaction ``kind`` values are deliberately small:

``deposit``
    Deposit and additions tables, plus positive uncategorized rows.
``withdrawal``
    Generic withdrawal rows and negative uncategorized rows.
``electronic_withdrawal``
    ACH, bill pay, Zelle, card payments and similar electronic withdrawals.
``card`` / ``atm``
    Debit card purchases and ATM activity.
``check``
    Paper checks. The check number is stored in ``extra["check_number"]``.
``fee``
    Service fees and other bank fees.
``interest``
    Interest paid rows.
``transfer``
    Online transfers between accounts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pdf_statement_parser.document import Document
from pdf_statement_parser.exceptions import ParseError
from pdf_statement_parser.models import AccountType, Statement, Transaction
from pdf_statement_parser.parsers.base import StatementParser
from pdf_statement_parser.utils import AMOUNT_PATTERN, parse_amount, parse_date, resolve_month_day

_PERIOD_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*through\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
_ACCOUNT_RE = re.compile(r"\b(?:Account Number:?\s*)?(\d{10,17})\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(AMOUNT_PATTERN)
_DATE_RE = re.compile(r"^(\d{2})/(\d{2})\b")
_DETAIL_ROW_RE = re.compile(
    rf"^(\d{{2}})/(\d{{2}})\s+(.+?)\s+({_AMOUNT_RE.pattern})\s+({_AMOUNT_RE.pattern})$"
)
_TABLE_ROW_RE = re.compile(rf"^(\d{{2}})/(\d{{2}})\s+(.+?)\s+({_AMOUNT_RE.pattern})$")
_CHECK_ROW_RE = re.compile(rf"^(\d+)\s+(\d{{2}})/(\d{{2}})\s+({_AMOUNT_RE.pattern})$")
_SUMMARY_AMOUNT_RE = re.compile(rf"^(.+?)\s+({_AMOUNT_RE.pattern})$")
_COUNTED_SUMMARY_RE = re.compile(rf"^(.+?)\s+(\d+)\s+({_AMOUNT_RE.pattern})$")

_PRODUCTS = (
    "Chase Business Complete Checking",
    "Chase Total Checking",
    "Chase Savings",
)
_SECTION_HEADERS = {
    "DEPOSITS AND ADDITIONS": ("deposit", Decimal("1")),
    "ATM & DEBIT CARD WITHDRAWALS": ("atm", Decimal("-1")),
    "ATM AND DEBIT CARD WITHDRAWALS": ("atm", Decimal("-1")),
    "ELECTRONIC WITHDRAWALS": ("electronic_withdrawal", Decimal("-1")),
    "OTHER WITHDRAWALS": ("withdrawal", Decimal("-1")),
    "CARD PURCHASES": ("card", Decimal("-1")),
    "CHECKS PAID": ("check", Decimal("-1")),
    "FEES": ("fee", Decimal("-1")),
    "INTEREST PAID": ("interest", Decimal("1")),
}
_END_MARKERS = (
    "DAILY ENDING BALANCE",
    "IN CASE OF ERRORS",
    "ACCOUNT ACTIVITY",
    "IMPORTANT INFORMATION",
    "WANT TO AVOID",
)


@dataclass(frozen=True)
class _Line:
    index: int
    page: int
    text: str


@dataclass
class _ParsedRow:
    date: date
    description: str
    amount: Decimal
    balance: Decimal | None = None
    kind: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _ConsolidatedRow:
    account_name: str
    account_number: str
    opening_balance: Decimal
    closing_balance: Decimal


class ChaseDepositParser(StatementParser):
    id = "chase-deposit"
    bank = "Chase"
    description = (
        "Chase checking and savings, personal and business, including consolidated statements"
    )

    def detect(self, doc: Document) -> float:
        if not doc.pages:
            return 0.0
        text = doc.pages[0].text
        upper = text.upper()
        if "PAYMENT DUE DATE" in upper or "MINIMUM PAYMENT DUE" in upper:
            return 0.0
        if "CARDMEMBER SERVICE" in upper or "WWW.CHASE.COM/CARDHELP" in upper:
            return 0.0
        if "JPMORGAN CHASE BANK, N.A." not in upper:
            return 0.0
        if "CUSTOMER SERVICE INFORMATION" not in upper:
            return 0.0
        if not _PERIOD_RE.search(text):
            return 0.0
        if any(product.upper() in upper for product in _PRODUCTS):
            return 0.98
        return 0.92

    def parse(self, doc: Document) -> list[Statement]:
        lines = _document_lines(doc)
        text = "\n".join(line.text for line in lines)
        period = _parse_period(text)
        sections = _find_account_sections(lines)
        if not sections:
            raise ParseError("no Chase deposit account sections found")

        document_holder = _extract_document_holder(lines)
        consolidated_rows = _parse_consolidated_summary(lines)
        statements: list[Statement] = []
        for start, end, product in sections:
            statements.append(_parse_account(lines, start, end, product, period, document_holder))
        _add_consolidated_warnings(statements, consolidated_rows)
        return statements


def _document_lines(doc: Document) -> list[_Line]:
    lines: list[_Line] = []
    idx = 0
    for page in doc.pages:
        for raw in page.lines:
            text = _clean_line(raw)
            if text:
                lines.append(_Line(idx, page.number, text))
                idx += 1
    return lines


def _clean_line(line: str) -> str:
    line = line.replace("\u2019", "'").replace("\u0092", "'")
    return " ".join(line.split())


def _parse_period(text: str) -> tuple[date, date]:
    match = _PERIOD_RE.search(text)
    if not match:
        raise ParseError("statement period not found")
    return parse_date(match.group(1)), parse_date(match.group(2))


def _find_account_sections(lines: list[_Line]) -> list[tuple[int, int, str]]:
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        product = _product_name(line.text)
        if product is None:
            continue
        if _has_summary_after(lines, i):
            starts.append((i, product))

    sections: list[tuple[int, int, str]] = []
    for pos, (start, product) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        sections.append((start, end, product))
    return sections


def _product_name(text: str) -> str | None:
    normalized = text.casefold()
    for product in _PRODUCTS:
        if normalized == product.casefold():
            return product
    return None


def _has_summary_after(lines: list[_Line], start: int) -> bool:
    for line in lines[start + 1 : min(start + 10, len(lines))]:
        upper = line.text.upper()
        if "CHECKING SUMMARY" in upper or "SAVINGS SUMMARY" in upper:
            return True
    return False


def _parse_account(
    lines: list[_Line],
    start: int,
    end: int,
    product: str,
    period: tuple[date, date],
    document_holder: str | None,
) -> Statement:
    period_start, period_end = period
    account_number = _extract_account_number(lines, start, end)
    account_holder = _extract_account_holder(lines, start, end, document_holder)
    account_type = AccountType.SAVINGS if "SAVINGS" in product.upper() else AccountType.CHECKING
    summary = _parse_summary(lines, start, end)
    transactions = _parse_transactions(lines[start:end], period)
    statement = Statement(
        bank="Chase",
        account_type=account_type,
        account_number=account_number,
        period_start=period_start,
        period_end=period_end,
        account_name=product,
        account_holder=account_holder,
        opening_balance=summary.opening_balance,
        closing_balance=summary.closing_balance,
        total_deposits=summary.total_deposits,
        total_expenses=summary.total_expenses,
        transactions=transactions,
        extra=summary.extra,
    )
    if account_holder is None:
        statement.warnings.append("account holder not found")
    if account_number is None:
        statement.warnings.append("account number not found")
    return statement


def _extract_account_number(lines: list[_Line], start: int, end: int) -> str | None:
    for line in lines[max(0, start - 8) : min(end, start + 12)]:
        if "Account Number" in line.text:
            match = _ACCOUNT_RE.search(line.text)
            if match:
                return match.group(1)
            for next_line in lines[line.index + 1 : min(end, line.index + 5)]:
                match = _ACCOUNT_RE.fullmatch(next_line.text)
                if match:
                    return match.group(1)
    for line in lines[start:end]:
        if "Account Number" not in line.text:
            continue
        match = _ACCOUNT_RE.search(line.text)
        if match:
            return match.group(1)
    section_page = lines[start].page
    for line in reversed(lines[max(0, start - 80) : start]):
        if line.page != section_page:
            break
        match = re.fullmatch(r"\d{10,17}", line.text)
        if match:
            return match.group(0)
    return None


def _extract_account_holder(
    lines: list[_Line], start: int, end: int, document_holder: str | None
) -> str | None:
    for line in lines[max(0, start - 8) : min(end, start + 10)]:
        if "Account Number" not in line.text:
            continue
        holder = line.text.split("Account Number", 1)[0].strip(" :-")
        holder = _clean_holder_line(holder)
        if holder and _looks_like_holder(holder):
            return holder
    return document_holder


def _extract_document_holder(lines: list[_Line]) -> str | None:
    for i, line in enumerate(lines[:120]):
        if "DRE" not in line.text:
            continue
        block: list[str] = []
        for candidate in lines[i + 1 : min(i + 12, len(lines))]:
            text = _clean_holder_line(candidate.text)
            if _is_holder_hard_stop(text):
                break
            if _is_service_line(text):
                continue
            if _is_address_start(text):
                break
            if _looks_like_holder(text):
                block.append(text)
                continue
            if block:
                break
        if block:
            return ", ".join(block)
    return None


def _is_holder_hard_stop(text: str) -> bool:
    upper = text.upper()
    if not text:
        return False
    if text.startswith("*start*") or text.startswith("*end*"):
        return True
    if "SUMMARY" in upper or "CONSOLIDATED BALANCE" in upper:
        return True
    return _product_name(text) is not None


def _clean_holder_line(text: str) -> str:
    if text.startswith("*start*") or text.startswith("*end*"):
        return ""
    service_phrases = (
        "International Calls|Para Espanol|Deaf and Hard of Hearing|We accept operator relay calls"
    )
    text = re.sub(
        rf"\b(?:{service_phrases}):?.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip(" :-")


def _is_service_line(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:International Calls|Para Espanol|Deaf and Hard of Hearing|"
            r"We accept operator relay calls|Service Center|Web site)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _is_address_start(text: str) -> bool:
    upper = text.upper()
    return bool(re.match(r"^\d", text) or upper.startswith(("P O BOX", "PO BOX")))


def _looks_like_holder(text: str) -> bool:
    upper = text.upper()
    if not text or any(char.isdigit() for char in text):
        return False
    if text.startswith("*start*") or text.startswith("*end*"):
        return False
    if upper in {"SM", "SM SM", "®", "® ®", "•", "• •"}:
        return False
    if any(symbol in text for symbol in ("®", "•", "*")):
        return False
    if re.search(r"[.!?]", text):
        return False
    blocked = (
        "CUSTOMER SERVICE",
        "JPMORGAN",
        "CHASE",
        "P O BOX",
        "WEB SITE",
        "SERVICE CENTER",
        "ACCOUNT",
        "PRIMARY",
        "PAGE OF",
        "QUESTIONS",
        "PARTICIPATING ONLINE BUSINESSES",
        "THIS CHANGE",
    )
    if any(token in upper for token in blocked):
        return False
    return not re.search(r"\b[A-Z]{2}\b\s*\d{5}", upper)


def _parse_consolidated_summary(lines: list[_Line]) -> list[_ConsolidatedRow]:
    rows: list[_ConsolidatedRow] = []
    in_summary = False
    products = "|".join(re.escape(product) for product in _PRODUCTS)
    row_re = re.compile(
        rf"^({products})\s+(\d{{10,17}})\s+({_AMOUNT_RE.pattern})\s+({_AMOUNT_RE.pattern})$",
        re.IGNORECASE,
    )
    for line in lines:
        upper = line.text.upper()
        if "CONSOLIDATED BALANCE SUMMARY" in upper:
            in_summary = True
            continue
        if not in_summary:
            continue
        if _product_name(line.text) is not None:
            break
        match = row_re.match(line.text)
        if match:
            account_name, account_number, opening, closing = match.groups()
            rows.append(
                _ConsolidatedRow(
                    account_name=_canonical_product_name(account_name),
                    account_number=account_number,
                    opening_balance=parse_amount(opening),
                    closing_balance=parse_amount(closing),
                )
            )
    return rows


def _canonical_product_name(value: str) -> str:
    for product in _PRODUCTS:
        if value.casefold() == product.casefold():
            return product
    return value


def _add_consolidated_warnings(
    statements: list[Statement], consolidated_rows: list[_ConsolidatedRow]
) -> None:
    if not consolidated_rows:
        return
    if len(statements) != len(consolidated_rows):
        warning = (
            f"parsed account count {len(statements)} differs from consolidated summary count "
            f"{len(consolidated_rows)}"
        )
        for statement in statements:
            statement.warnings.append(warning)

    by_account = {statement.account_number: statement for statement in statements}
    for row in consolidated_rows:
        matched_statement = by_account.get(row.account_number)
        if matched_statement is None:
            continue
        if (
            matched_statement.opening_balance != row.opening_balance
            or matched_statement.closing_balance != row.closing_balance
        ):
            matched_statement.warnings.append(
                "account balances differ from consolidated balance summary"
            )


@dataclass
class _Summary:
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    total_deposits: Decimal = Decimal("0")
    total_expenses: Decimal = Decimal("0")
    interest_paid: Decimal = Decimal("0")
    has_deposits_line: bool = False
    extra: dict[str, object] = field(default_factory=dict)


def _parse_summary(lines: list[_Line], start: int, end: int) -> _Summary:
    summary = _Summary()
    in_summary = False
    breakdown: dict[str, str] = {}
    counts: dict[str, int] = {}
    for line in lines[start:end]:
        upper = line.text.upper()
        if "CHECKING SUMMARY" in upper or "SAVINGS SUMMARY" in upper:
            in_summary = True
            continue
        if not in_summary:
            continue
        if _is_transaction_area_start(upper):
            break
        parsed = _parse_summary_line(line.text)
        if parsed is None:
            continue
        label, amount, count = parsed
        norm = _normalize_label(label)
        breakdown[norm] = str(amount)
        if count is not None:
            counts[norm] = count
        if norm == "beginning balance":
            summary.opening_balance = amount
        elif norm == "ending balance":
            summary.closing_balance = amount
        elif norm == "deposits and additions":
            summary.has_deposits_line = True
            summary.total_deposits += abs(amount)
        elif norm in {"interest paid", "interest paid this period"}:
            summary.interest_paid += abs(amount)
        elif _is_expense_summary(norm):
            summary.total_expenses += abs(amount)
        elif norm in {"annual percentage yield earned", "interest rate"}:
            summary.extra[norm.replace(" ", "_")] = str(amount)

    if summary.interest_paid:
        summary.extra["interest_paid"] = str(summary.interest_paid)
        if _should_add_interest_to_deposits(summary):
            summary.total_deposits += summary.interest_paid
    summary.extra["summary"] = breakdown
    if counts:
        summary.extra["transaction_counts"] = counts
    return summary


def _parse_summary_line(text: str) -> tuple[str, Decimal, int | None] | None:
    match = _COUNTED_SUMMARY_RE.match(text)
    if match:
        label, count, amount_text = match.groups()
        return label.strip(), parse_amount(amount_text), int(count)
    match = _SUMMARY_AMOUNT_RE.match(text)
    if match:
        label, amount_text = match.groups()
        return label.strip(), parse_amount(amount_text), None
    return None


def _normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


def _is_deposit_summary(label: str) -> bool:
    return label in {
        "deposits and additions",
        "interest paid",
        "interest paid this period",
    }


def _should_add_interest_to_deposits(summary: _Summary) -> bool:
    if not summary.has_deposits_line:
        return True
    if summary.opening_balance is None or summary.closing_balance is None:
        return False
    without_interest = summary.opening_balance + summary.total_deposits - summary.total_expenses
    with_interest = without_interest + summary.interest_paid
    return abs(summary.closing_balance - with_interest) < abs(
        summary.closing_balance - without_interest
    )


def _is_expense_summary(label: str) -> bool:
    return any(
        token in label
        for token in (
            "withdrawals",
            "checks paid",
            "fees",
            "card purchases",
            "atm",
        )
    )


def _is_transaction_area_start(upper: str) -> bool:
    if "TRANSACTION DETAIL" in upper:
        return True
    return any(upper == header for header in _SECTION_HEADERS)


def _parse_transactions(lines: list[_Line], period: tuple[date, date]) -> list[Transaction]:
    detail = _parse_transaction_detail(lines, period)
    if detail:
        return [_to_transaction(row) for row in detail]
    rows = _parse_section_tables(lines, period)
    return [_to_transaction(row) for row in rows]


def _parse_transaction_detail(lines: list[_Line], period: tuple[date, date]) -> list[_ParsedRow]:
    rows: list[_ParsedRow] = []
    active: _ParsedRow | None = None
    in_detail = False
    header_seen = False
    for line in lines:
        upper = line.text.upper()
        if "TRANSACTION DETAIL" in upper:
            in_detail = True
            header_seen = False
            continue
        if not in_detail:
            continue
        if upper == "DATE DESCRIPTION AMOUNT BALANCE":
            header_seen = True
            continue
        if "ENDING BALANCE" in upper:
            active = None
            in_detail = False
            continue
        if not header_seen:
            continue
        text = _row_text(line.text, period)
        match = _DETAIL_ROW_RE.match(text) if text is not None else None
        if match:
            month, day, description, amount_text, balance_text = match.groups()
            row = _ParsedRow(
                date=_resolve_date(month, day, period),
                description=description.strip(),
                amount=parse_amount(amount_text),
                balance=parse_amount(balance_text),
            )
            row.kind = _kind_from_description(row.description, row.amount)
            _add_check_number(row)
            rows.append(row)
            active = row
        elif active is not None and text is None and not _skip_transaction_line(line.text):
            active.description = f"{active.description} {line.text}".strip()
            active.kind = _kind_from_description(active.description, active.amount)
            _add_check_number(active)
    return rows


def _parse_section_tables(lines: list[_Line], period: tuple[date, date]) -> list[_ParsedRow]:
    rows: list[_ParsedRow] = []
    active: _ParsedRow | None = None
    section_kind: str | None = None
    section_sign = Decimal("1")
    in_table = False

    for line in lines:
        upper = line.text.upper()
        header = _table_header(upper)
        if header is not None:
            section_kind, section_sign = header
            in_table = False
            active = None
            continue
        if section_kind is None:
            continue
        if upper in {"DATE DESCRIPTION AMOUNT", "CHECK NUMBER DATE AMOUNT"}:
            in_table = True
            continue
        if upper.startswith("TOTAL "):
            section_kind = None
            in_table = False
            active = None
            continue
        if any(upper.startswith(marker) for marker in _END_MARKERS):
            section_kind = None
            in_table = False
            active = None
            continue
        if not in_table:
            continue

        text = _row_text(line.text, period)
        row = (
            _parse_section_row(text, period, section_kind, section_sign)
            if text is not None
            else None
        )
        if row is not None:
            rows.append(row)
            active = row
        elif active is not None and text is None and not _skip_transaction_line(line.text):
            active.description = f"{active.description} {line.text}".strip()
    return rows


def _table_header(upper: str) -> tuple[str, Decimal] | None:
    if upper in _SECTION_HEADERS:
        return _SECTION_HEADERS[upper]
    return None


def _parse_section_row(
    text: str,
    period: tuple[date, date],
    section_kind: str,
    sign: Decimal,
) -> _ParsedRow | None:
    if section_kind == "check":
        check_match = _CHECK_ROW_RE.match(text)
        if not check_match:
            return None
        check_number, month, day, amount_text = check_match.groups()
        amount = abs(parse_amount(amount_text)) * sign
        return _ParsedRow(
            date=_resolve_date(month, day, period),
            description=f"Check # {check_number}",
            amount=amount,
            kind="check",
            extra={"check_number": check_number},
        )

    match = _TABLE_ROW_RE.match(text)
    if not match:
        return None
    month, day, description, amount_text = match.groups()
    amount = abs(parse_amount(amount_text)) * sign
    kind = (
        _kind_from_description(description, amount) if section_kind == "deposit" else section_kind
    )
    row = _ParsedRow(
        date=_resolve_date(month, day, period),
        description=description.strip(),
        amount=amount,
        kind=kind,
    )
    _add_check_number(row)
    return row


def _skip_transaction_line(text: str) -> bool:
    upper = text.upper()
    if not text or text.startswith("*"):
        return True
    if upper in {"(CONTINUED)", "BEGINNING BALANCE"}:
        return True
    if upper.startswith("BEGINNING BALANCE"):
        return True
    if upper.startswith("PAGE OF"):
        return True
    if _PERIOD_RE.search(text) or "Account Number" in text:
        return True
    return _product_name(text) is not None


def _row_text(text: str, period: tuple[date, date]) -> str | None:
    if _DATE_RE.match(text):
        return text
    if _CHECK_ROW_RE.match(text):
        return text
    match = re.search(r"(?<!\d)(\d{1,2}/\d{2})\b", text)
    if match:
        row = text[match.start() :]
        if row[1:2] != "/":
            return row
        return f"{_missing_month_digit(row, period)}{row}"
    return None


def _missing_month_digit(row: str, period: tuple[date, date]) -> str:
    ones = int(row[0])
    months = {period[0].month, period[1].month}
    for prefix in ("0", "1"):
        month = int(f"{prefix}{ones}")
        if month in months:
            return prefix
    return "0"


def _resolve_date(month: str, day: str, period: tuple[date, date]) -> date:
    return resolve_month_day(int(month), int(day), period[0], period[1])


def _kind_from_description(description: str, amount: Decimal) -> str:
    upper = description.upper()
    if "CHECK #" in upper:
        return "check"
    if "FEE" in upper:
        return "fee"
    if "INTEREST" in upper:
        return "interest"
    if "ATM" in upper:
        return "atm"
    if "CARD PURCHASE" in upper or "DEBIT CARD" in upper:
        return "card"
    if "TRANSFER" in upper:
        return "transfer"
    if "ZELLE" in upper or "ACH" in upper or "PPD ID" in upper or "WEB ID" in upper:
        return "deposit" if amount > 0 else "electronic_withdrawal"
    return "deposit" if amount > 0 else "withdrawal"


def _add_check_number(row: _ParsedRow) -> None:
    match = re.search(r"\bCheck\s*#\s*(\d+)\b", row.description, re.IGNORECASE)
    if match:
        row.kind = "check"
        row.extra["check_number"] = match.group(1)


def _to_transaction(row: _ParsedRow) -> Transaction:
    return Transaction(
        date=row.date,
        description=row.description,
        amount=row.amount,
        balance=row.balance,
        kind=row.kind,
        extra=dict(row.extra),
    )
