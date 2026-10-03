"""Chase credit card statements.

Chase summary totals are printed by statement section, and this parser uses
those printed totals for validation. A purchase refund is printed as a negative
amount inside the purchase section, so the ACCOUNT SUMMARY reports net
purchases. The core model checks gross positive and negative transaction sums,
so the parser splits those net section totals only for opposite-signed lines:
a purchase refund adds to deposits and is added back to expenses. That keeps
the totals tied to ACCOUNT SUMMARY while making missed transactions fail
reconcile().
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pdf_statement_parser.document import Document
from pdf_statement_parser.exceptions import ParseError
from pdf_statement_parser.models import AccountType, Statement, Transaction
from pdf_statement_parser.parsers.base import StatementParser
from pdf_statement_parser.utils import parse_amount, parse_date, resolve_month_day

_DATE_RE = r"\d{1,2}/\d{1,2}/\d{2,4}"
_MONEY_RE = re.compile(r"[-+]?\$?[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?")
_SUMMARY_AMOUNT_RE = re.compile(r"[-+]?\$?[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}")
_TX_AMOUNT_PATTERN = r"[-+]?\$?[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)?\.\d{2})"
_AMOUNT_AT_END_RE = re.compile(
    rf"^(?P<month>\d{{1,2}})/(?P<day>\d{{1,2}})\s+"
    rf"(?P<description>.+?)\s+(?P<amount>{_TX_AMOUNT_PATTERN})\s*$"
)
_SECTION_KINDS = {
    "PAYMENTS AND OTHER CREDITS": "credit",
    "PURCHASE": "purchase",
    "PURCHASES": "purchase",
    "FEES CHARGED": "fee",
    "INTEREST CHARGED": "interest",
    "CASH ADVANCE": "cash_advance",
    "CASH ADVANCES": "cash_advance",
    "BALANCE TRANSFER": "balance_transfer",
    "BALANCE TRANSFERS": "balance_transfer",
}
_STOP_ACTIVITY_PREFIXES = (
    "INTEREST CHARGES",
    "YEAR-TO-DATE TOTALS",
    "YOUR ANNUAL PERCENTAGE RATE",
)
_SKIP_ACTIVITY_PREFIXES = (
    "DATE OF",
    "TRANSACTION MERCHANT NAME",
    "INCLUDING PAYMENTS RECEIVED",
    "TRANSACTIONS THIS CYCLE",
)


@dataclass(frozen=True)
class _Summary:
    previous_balance: Decimal
    payment_credits: Decimal
    purchases: Decimal
    cash_advances: Decimal
    balance_transfers: Decimal
    fees_charged: Decimal
    interest_charged: Decimal
    new_balance: Decimal

    @property
    def net_printed_activity(self) -> Decimal:
        return (
            self.payment_credits
            + self.purchases
            + self.cash_advances
            + self.balance_transfers
            + self.fees_charged
            + self.interest_charged
        )


class ChaseCreditCardParser(StatementParser):
    id = "chase-credit-card"
    bank = "Chase"
    description = (
        "Chase personal credit cards (Freedom, Sapphire, Prime Visa, United, Southwest, ...)"
    )

    def detect(self, doc: Document) -> float:
        if not doc.pages:
            return 0.0
        text = doc.pages[0].text
        upper = text.upper()
        if "CHECKING SUMMARY" in upper or "SAVINGS SUMMARY" in upper:
            return 0.0
        if "JPMORGAN CHASE BANK, N.A." in upper and "ACCOUNT ACTIVITY" not in upper:
            return 0.0

        score = 0.0
        if "OPENING/CLOSING DATE" in upper:
            score += 0.28
        if "PREVIOUS BALANCE" in upper and "NEW BALANCE" in upper:
            score += 0.24
        if "ACCOUNT NUMBER" in upper and ("CREDIT LIMIT" in upper or "CREDIT ACCESS LINE" in upper):
            score += 0.18
        if (
            "CHASE.COM/CARD" in upper
            or "CHASE.COM/AMAZON" in upper
            or "CHASE.COM/UNITED" in upper
            or "CHASE.COM/SOUTHWEST" in upper
            or "CARDMEMBER SERVICE" in upper
        ):
            score += 0.22
        if "PAYMENT DUE DATE" in upper or "MINIMUM PAYMENT" in upper:
            score += 0.10
        return min(score, 0.99) if score >= 0.7 else 0.0

    def parse(self, doc: Document) -> list[Statement]:
        if not doc.pages:
            raise ParseError("empty document")

        page1 = doc.pages[0].text
        all_text = doc.text
        summary = _parse_summary(page1)
        period_start, period_end = _parse_period(page1)
        account_number = _parse_account_number(page1)
        account_holder = _parse_account_holder(doc, page1)
        transactions = _parse_transactions(doc, period_start, period_end, account_holder)
        warnings: list[str] = []

        if account_number is None:
            warnings.append("account number not found")
        if account_holder is None:
            warnings.append("account holder not found")

        total_deposits, total_expenses = _summary_totals(summary, transactions)

        net_transactions_printed = -sum((t.amount for t in transactions), Decimal("0"))
        if transactions and abs(net_transactions_printed - summary.net_printed_activity) > Decimal(
            "0.01"
        ):
            warnings.append(
                "transaction total differs from account summary by "
                f"{summary.net_printed_activity - net_transactions_printed}"
            )

        statement = Statement(
            bank=self.bank,
            account_type=AccountType.CREDIT_CARD,
            account_number=account_number,
            account_name=_parse_account_name(page1),
            account_holder=account_holder,
            period_start=period_start,
            period_end=period_end,
            opening_balance=summary.previous_balance,
            closing_balance=summary.new_balance,
            total_deposits=total_deposits,
            total_expenses=total_expenses,
            transactions=transactions,
            extra=_parse_extra(page1, all_text, period_start, period_end),
            warnings=warnings,
        )
        return [statement]


def _parse_summary(text: str) -> _Summary:
    lines = _summary_lines(text.splitlines())
    try:
        return _Summary(
            previous_balance=_find_summary_amount(lines, "previousbalance"),
            payment_credits=_find_summary_amount(lines, "paymentcredits"),
            purchases=_find_summary_amount(lines, "purchases"),
            cash_advances=_find_summary_amount(lines, "cashadvances"),
            balance_transfers=_find_summary_amount(lines, "balancetransfers"),
            fees_charged=_find_summary_amount(lines, "feescharged"),
            interest_charged=_find_summary_amount(lines, "interestcharged"),
            new_balance=_find_summary_amount(lines, "newbalance"),
        )
    except ValueError as exc:
        raise ParseError(str(exc)) from exc


def _summary_lines(lines: list[str]) -> list[str]:
    start = 0
    for index, line in enumerate(lines):
        if "previousbalance" in _letters_only(line):
            start = index
            break
    end = len(lines)
    for index, line in enumerate(lines[start:], start=start):
        if "youraccountmessages" in _letters_only(line):
            end = index
            break
    return lines[start:end]


def _find_summary_amount(lines: list[str], key: str) -> Decimal:
    for line in lines:
        if not _letters_only(line).startswith(key):
            continue
        match = _SUMMARY_AMOUNT_RE.search(line)
        if match:
            return _parse_money(match.group(0))
    raise ValueError(f"missing summary amount {key!r}")


def _summary_totals(summary: _Summary, transactions: list[Transaction]) -> tuple[Decimal, Decimal]:
    deposits = Decimal("0")
    expenses = Decimal("0")

    credit_reversals = _printed_positive(transactions, "credit")
    credit_payments = summary.payment_credits - credit_reversals
    if credit_payments < 0:
        deposits += -credit_payments
    expenses += credit_reversals

    for section, net_total in (
        ("purchase", summary.purchases),
        ("cash_advance", summary.cash_advances),
        ("balance_transfer", summary.balance_transfers),
        ("fee", summary.fees_charged),
        ("interest", summary.interest_charged),
    ):
        refunds = _printed_negative_abs(transactions, section)
        deposits += refunds
        expenses += max(net_total + refunds, Decimal("0"))

    return deposits, expenses


def _printed_positive(transactions: list[Transaction], section: str) -> Decimal:
    return sum(
        (
            printed
            for transaction in transactions
            if transaction.extra.get("summary_section") == section
            if (printed := _transaction_printed_amount(transaction)) is not None and printed > 0
        ),
        Decimal("0"),
    )


def _printed_negative_abs(transactions: list[Transaction], section: str) -> Decimal:
    return sum(
        (
            -printed
            for transaction in transactions
            if transaction.extra.get("summary_section") == section
            if (printed := _transaction_printed_amount(transaction)) is not None and printed < 0
        ),
        Decimal("0"),
    )


def _transaction_printed_amount(transaction: Transaction) -> Decimal | None:
    value = transaction.extra.get("printed_amount")
    return value if isinstance(value, Decimal) else None


def _parse_period(text: str) -> tuple[date, date]:
    match = re.search(
        rf"Opening/Closing\s+Date\s+({_DATE_RE})\s*-\s*({_DATE_RE})",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise ParseError("missing opening/closing date")
    return parse_date(match.group(1)), parse_date(match.group(2))


def _parse_account_number(text: str) -> str | None:
    for line in text.splitlines():
        match = re.search(
            r"Account\s+[Nn]umber:\s*((?:[Xx\d]{4}\s*){2,5})",
            line,
            re.IGNORECASE,
        )
        if match:
            return re.sub(r"\s+", "", match.group(1).upper())
    return None


def _parse_account_name(text: str) -> str:
    upper = text.upper()
    compact = " ".join(upper.split())
    if "PRIME VISA" in upper or "CHASE.COM/AMAZON" in upper:
        return "Prime Visa"
    if "SOUTHWEST" in upper or "RAPID REWARDS" in upper:
        return "Southwest Rapid Rewards"
    if "UNITED" in upper or "MILEAGEPLUS" in upper:
        if "UNITED QUEST" in upper:
            return "United Quest MileagePlus"
        return "United MileagePlus"
    if "FREEDOM UNLIMITED" in upper:
        return "Freedom Unlimited"
    if "CHASE FREEDOM:" in upper or "CHASE FREEDOM " in upper:
        return "Freedom"
    if "INK" in upper or "OFC SPLY" in upper or "INTERNET,CABLE,PHONE" in compact:
        return "Ink Business Cash"
    if "SAPPHIRE PREFERRED" in upper:
        return "Sapphire Preferred"
    if "SAPPHIRE RESERVE" in upper:
        return "Sapphire Reserve"
    if "SAPPHIRE" in upper:
        return "Sapphire"
    if "ULTIMATE REWARDS" in upper:
        return "Ultimate Rewards"
    return "Chase Credit Card"


def _parse_account_holder(doc: Document, page1: str) -> str | None:
    for page in doc.pages:
        for line in page.lines:
            match = re.match(
                r"^([A-Z][A-Z .,&'-]{2,80})\s+Page\d+\s+of\s+\d+\s+Statement Date:",
                line,
            )
            if match:
                holder = _clean_holder(match.group(1))
                if holder:
                    return holder

    lines = page1.splitlines()
    for index, line in enumerate(lines):
        if "Amount Enclosed" not in line:
            continue
        for candidate in lines[index + 1 : index + 8]:
            holder = _clean_holder(candidate)
            if holder:
                return holder
    return None


def _clean_holder(line: str) -> str | None:
    value = " ".join(line.split())
    upper = value.upper()
    if not value or not re.search(r"[A-Z]", upper):
        return None
    if any(ch.isdigit() for ch in value) or "$" in value:
        return None
    rejected = (
        "AUTOPAY",
        "AMOUNT ENCLOSED",
        "CARDMEMBER SERVICE",
        "PO BOX",
        "P.O. BOX",
        "WILMINGTON",
        "CAROL STREAM",
        "PAYMENT DUE DATE",
        "ACCOUNT NUMBER",
    )
    if any(token in upper for token in rejected):
        return None
    words = [word for word in re.split(r"[\s,&'-]+", upper) if word]
    if len(words) < 2:
        return None
    return value


def _parse_transactions(
    doc: Document,
    period_start: date,
    period_end: date,
    account_holder: str | None,
) -> list[Transaction]:
    transactions: list[Transaction] = []
    in_activity = False
    section: str | None = None
    holder_upper = account_holder.upper() if account_holder else None

    for page in doc.pages:
        lines = page.lines
        for index, raw_line in enumerate(lines):
            line = " ".join(raw_line.split())
            if not line:
                continue
            upper = line.upper()

            if "ACCOUNT ACTIVITY" in upper:
                in_activity = True
                section = None
                continue
            if not in_activity:
                continue
            if _is_activity_stop(upper):
                in_activity = False
                section = None
                continue
            if _should_skip_activity_line(upper, holder_upper):
                continue

            next_upper = ""
            if index + 1 < len(lines):
                next_upper = " ".join(lines[index + 1].split()).upper()
            if next_upper.startswith("TRANSACTIONS THIS CYCLE"):
                continue

            parsed_section = _parse_section(upper)
            if parsed_section is not None:
                section = parsed_section
                continue

            match = _AMOUNT_AT_END_RE.match(line)
            if match:
                printed = _parse_money(match.group("amount"))
                month = int(match.group("month"))
                day = int(match.group("day"))
                txn_date = _resolve_transaction_date(month, day, period_start, period_end)
                kind = _kind_for_transaction(section, match.group("description"), printed)
                transactions.append(
                    Transaction(
                        date=txn_date,
                        description=match.group("description").strip(),
                        amount=-printed,
                        kind=kind,
                        extra={"printed_amount": printed, "summary_section": section},
                    )
                )
                continue

            if transactions and _is_continuation_line(line, upper):
                _attach_continuation(transactions[-1], line)

    return transactions


def _resolve_transaction_date(
    month: int,
    day: int,
    period_start: date,
    period_end: date,
) -> date:
    txn_date = resolve_month_day(month, day, period_start, period_end)
    if txn_date <= period_end or day > 12:
        return txn_date
    swapped = resolve_month_day(day, month, period_start, period_end)
    if period_start <= swapped <= period_end:
        return swapped
    return txn_date


def _is_activity_stop(upper: str) -> bool:
    if re.match(r"^\d{4}\s+TOTALS\s+YEAR-TO-DATE", upper):
        return True
    return upper.startswith(_STOP_ACTIVITY_PREFIXES)


def _should_skip_activity_line(upper: str, holder_upper: str | None) -> bool:
    if upper.startswith(_SKIP_ACTIVITY_PREFIXES):
        return True
    if " STATEMENT DATE:" in upper or "THIS STATEMENT IS A FACSIMILE" in upper:
        return True
    if upper.startswith("000000") or upper.startswith("PAGE "):
        return True
    return holder_upper is not None and upper == holder_upper


def _parse_section(upper: str) -> str | None:
    section_text = re.sub(r"\s*\(CONTINUED\)\s*", "", upper).strip()
    return _SECTION_KINDS.get(section_text)


def _kind_for_transaction(section: str | None, description: str, printed: Decimal) -> str:
    upper = description.upper()
    if "PAYMENT" in upper and ("THANK YOU" in upper or "AUTOMATIC" in upper):
        return "payment" if printed < 0 else "payment_reversal"
    if section == "credit":
        return "credit" if printed < 0 else "credit_reversal"
    if printed < 0:
        return "refund"
    return section or "purchase"


def _is_continuation_line(line: str, upper: str) -> bool:
    if _AMOUNT_AT_END_RE.match(line):
        return False
    if _is_activity_stop(upper) or _parse_section(upper) is not None:
        return False
    if _should_skip_activity_line(upper, None):
        return False
    return not (
        _MONEY_RE.search(line) and not upper.startswith(("ORDER NUMBER", "REFERENCE NUMBER"))
    )


def _attach_continuation(transaction: Transaction, line: str) -> None:
    if line.upper().startswith("ORDER NUMBER"):
        transaction.extra["order_number"] = line.split(maxsplit=2)[-1]
        return
    transaction.description = f"{transaction.description} {line}"


def _parse_extra(
    page1: str,
    all_text: str,
    period_start: date,
    period_end: date,
) -> dict[str, object]:
    extra: dict[str, object] = {}
    due_date = _search_date(
        r"Payment Due Date:\s*(" + _DATE_RE + ")",
        page1,
        period_start,
        period_end,
    )
    if due_date is not None:
        extra["payment_due_date"] = due_date
    statement_date = _search_date(
        r"Statement Date:\s*(" + _DATE_RE + ")",
        all_text,
        period_start,
        period_end,
    )
    if statement_date is not None:
        extra["statement_date"] = statement_date

    minimum_due = _search_money(r"Minimum Payment(?: Due)?:\s*(" + _MONEY_RE.pattern + ")", page1)
    if minimum_due is not None:
        extra["minimum_payment_due"] = minimum_due
    for key, label in (
        ("credit_limit", r"Credit Limit"),
        ("credit_access_line", r"Credit Access Line"),
        ("available_credit", r"Available Credit"),
        ("cash_access_line", r"Cash Access Line"),
        ("available_for_cash", r"Available for Cash"),
        ("past_due_amount", r"Past Due Amount"),
    ):
        value = _search_money(label + r"\s+(" + _MONEY_RE.pattern + ")", page1)
        if value is not None:
            extra[key] = value

    days_match = re.search(r"(\d+)\s+Days in Billing Period", all_text, re.IGNORECASE)
    if days_match:
        extra["days_in_billing_period"] = int(days_match.group(1))
    return extra


def _search_date(
    pattern: str,
    text: str,
    period_start: date,
    period_end: date,
) -> date | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    value = match.group(1)
    parts = value.split("/")
    if len(parts) == 3:
        return parse_date(value)
    return resolve_month_day(int(parts[0]), int(parts[1]), period_start, period_end)


def _search_money(pattern: str, text: str) -> Decimal | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return _parse_money(match.group(1))


def _parse_money(text: str) -> Decimal:
    value = text.strip()
    if value.startswith((".", "$.")):
        value = value.replace(".", "0.", 1)
    elif value.startswith(("+-.", "-+.", "-.", "+.")):
        value = value[0] + "0" + value[1:]
    if "." not in value:
        value += ".00"
    return parse_amount(value)


def _letters_only(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())
