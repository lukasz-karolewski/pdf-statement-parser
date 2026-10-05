"""Apple Card statements (card.apple.com / Wallet "Export statement" PDFs).

Apple Card prints two balances. The *monthly* balance ("Your March Balance")
is what is due this month: it includes this month's Apple Card Monthly
Installments but not the rest of the financed amount. The *total* balance also
includes every remaining installment. This parser rolls the monthly balance:
``opening_balance`` is "Previous Monthly Balance" (older statements say
"Prior"), ``closing_balance`` is "Your <Month> Balance", and each installment
plan contributes one ``kind="installment"`` transaction for this month's
installment. The total balances go to ``extra``.

Daily Cash is cash back paid to Apple Cash or Savings, not a points balance, so
it is reported in ``extra`` instead of ``Statement.rewards``: the statement
total as ``extra["daily_cash"]`` and per-transaction values in
``Transaction.extra``.

Statements print no card or account number. ``account_number`` is ``None`` and
``extra["not_printed"]`` lists it, which ``reconcile()`` honours.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from pdf_statement_parser.document import Document
from pdf_statement_parser.exceptions import ParseError
from pdf_statement_parser.models import AccountType, Statement, Transaction
from pdf_statement_parser.parsers.base import StatementParser
from pdf_statement_parser.utils import normalize_space, parse_amount

_AMOUNT = r"-?\$-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"
_FULL_DATE = r"\d{2}/\d{2}/\d{4}"
_MONTHS = {
    name: index
    for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
_MONTH_NAME = r"[A-Z][a-z]{2,8}\.?"

_PERIOD_RE = re.compile(
    rf"(?P<sm>{_MONTH_NAME})\s+(?P<sd>\d{{1,2}})\s*[\u2014\u2013-]\s*"
    rf"(?P<em>{_MONTH_NAME})\s+(?P<ed>\d{{1,2}}),\s*(?P<year>\d{{4}})"
)
_OWNER_RE = re.compile(r"^(?P<name>[^,@]+?),\s*\S+@\S+")
_OWNER_SECTION_RE = re.compile(
    r"^(?:Payments made by|Transactions by|Apple Card Installments by) (.+)$"
)
_TEXT_DATE_RE = re.compile(rf"(?P<m>{_MONTH_NAME})\s+(?P<d>\d{{1,2}}),\s*(?P<y>\d{{4}})")
_BALANCE_LINE_RE = re.compile(rf"^(?P<balance>{_AMOUNT})(?:\s+(?P<minimum>{_AMOUNT}))?")

_TX_RE = re.compile(
    rf"^(?P<date>{_FULL_DATE})\s+(?P<description>.+?)"
    rf"(?:\s+(?P<rate>-?\d+(?:\.\d+)?)%\s+(?P<cash>{_AMOUNT}))?"
    rf"\s+(?P<amount>{_AMOUNT})$"
)
_PAYMENT_RE = re.compile(
    rf"^(?P<date>{_FULL_DATE})\s+(?P<description>.+?)\s+(?P<amount>{_AMOUNT})$"
)
# "Promo Daily Cash 1% $0.44" or "Daily Cash Adjustment -3% $3.44" under a transaction.
_CASH_DETAIL_RE = re.compile(
    rf"^(?P<label>[A-Za-z][^$%]*?)\s+(?P<rate>-?\d+(?:\.\d+)?)%\s+(?P<cash>{_AMOUNT})$"
)
_PLAN_TOTALS = {
    "financed": "financed",
    "payments and credits": "paid_to_date",
    "remaining": "remaining",
}
_PLACEHOLDER_RE = re.compile(r"^—(?:\s+—)?(?:\s+\d+%)?(?:\s+-?\$0\.00)+$")

_PAGE_CHROME = (
    re.compile(r"^Statement$"),
    re.compile(r"^Apple Card(?: Co-Owners)?$"),
    re.compile(r"^Page \d+ ?/ ?\d+$"),
    re.compile(r"^Apple Card is issued by "),
    _OWNER_RE,
)


@dataclass
class _Summary:
    payments: Decimal | None = None
    charges: Decimal | None = None
    interest: Decimal | None = None
    installments: Decimal | None = None
    remaining_financed: Decimal | None = None
    daily_cash: Decimal | None = None


@dataclass
class _Plan:
    owner: str | None
    purchase_date: date
    description: str
    financed: Decimal
    daily_cash: Decimal | None = None
    daily_cash_rate: Decimal | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    installment: Decimal | None = None


class AppleCardParser(StatementParser):
    id = "apple-card"
    bank = "Apple"
    description = "Apple Card monthly statements, including co-owned accounts and installments"

    def detect(self, doc: Document) -> float:
        if not doc.pages:
            return 0.0
        text = doc.pages[0].text
        if "Apple Card" not in text:
            return 0.0
        score = 0.0
        if re.search(r"Apple Card is issued by", text):
            score += 0.3
        if re.search(r"(?:Previous|Prior) Monthly Balance", text):
            score += 0.25
        if re.search(r"Your \w+ Balance", text) and "Total Balance" in text:
            score += 0.2
        if _PERIOD_RE.search(text):
            score += 0.15
        if "card.apple.com" in text or "Apple Card Monthly Installments" in text:
            score += 0.1
        return min(score, 0.99) if score >= 0.7 else 0.0

    def parse(self, doc: Document) -> list[Statement]:
        if not doc.pages:
            raise ParseError("empty document")
        page1 = doc.pages[0].text
        all_text = doc.text
        warnings: list[str] = []

        period_start, period_end = _parse_period(page1)
        owners = _parse_owners(page1)
        opening = _search_amount(r"(?:Previous|Prior) Monthly Balance\s+(" + _AMOUNT + ")", page1)
        closing, minimum_due, due_date = _parse_monthly_balance(page1)
        if opening is None or closing is None:
            raise ParseError("monthly balance summary not found")

        summary = _parse_summary(all_text)
        transactions, plans = _parse_details(doc, period_end)
        transactions.extend(_installment_transactions(plans, period_end))
        transactions.extend(_parse_interest(all_text, period_end))

        warnings.extend(_check_sections(summary, transactions))
        if not owners:
            warnings.append("account holder not found")

        payments_in = _section_sum(transactions, "payment")
        refunds = _section_sum(transactions, "refund")
        charges_net = summary.charges if summary.charges is not None else Decimal("0")
        installments = (
            summary.installments
            if summary.installments is not None
            else -_section_sum(transactions, "installment")
        )
        interest = summary.interest if summary.interest is not None else Decimal("0")
        printed_payments = -summary.payments if summary.payments is not None else payments_in

        # The summary nets returns into "charges, credits, and returns"; split them back out
        # so both totals stay tied to printed figures and still catch a missed transaction.
        total_deposits = printed_payments + refunds
        total_expenses = charges_net + refunds + installments + interest

        extra = _parse_extra(page1, all_text, summary, plans, due_date, minimum_due, owners)
        warnings.extend(_check_total_balance(extra, summary))

        statement = Statement(
            bank=self.bank,
            account_type=AccountType.CREDIT_CARD,
            account_number=None,
            account_name="Apple Card",
            account_holder=", ".join(owners) if owners else None,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening,
            closing_balance=closing,
            total_deposits=total_deposits,
            total_expenses=total_expenses,
            transactions=transactions,
            extra=extra,
            warnings=warnings,
        )
        return [statement]


def _month(name: str) -> int:
    try:
        return _MONTHS[name.rstrip(".")[:3].lower()]
    except KeyError:
        raise ParseError(f"unknown month {name!r}") from None


def _text_date(match: re.Match[str]) -> date:
    return date(int(match.group("y")), _month(match.group("m")), int(match.group("d")))


def _parse_period(text: str) -> tuple[date, date]:
    m = _PERIOD_RE.search(text)
    if not m:
        raise ParseError("statement period not found")
    year = int(m.group("year"))
    end = date(year, _month(m.group("em")), int(m.group("ed")))
    start_month = _month(m.group("sm"))
    start_year = year - 1 if start_month > end.month else year
    return date(start_year, start_month, int(m.group("sd"))), end


def _parse_owners(page1: str) -> list[str]:
    owners: list[str] = []
    for line in page1.splitlines():
        m = _OWNER_RE.match(line.strip())
        if m:
            name = normalize_space(m.group("name"))
            if name not in owners:
                owners.append(name)
        elif owners:
            break
    return owners


def _parse_monthly_balance(page1: str) -> tuple[Decimal | None, Decimal | None, date | None]:
    lines = page1.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^Your \w+ Balance\b", line.strip()):
            continue
        for candidate in lines[index + 1 : index + 4]:
            m = _BALANCE_LINE_RE.match(candidate.strip())
            if not m:
                continue
            minimum = parse_amount(m.group("minimum")) if m.group("minimum") else None
            due = _TEXT_DATE_RE.search(candidate[m.end() :])
            return parse_amount(m.group("balance")), minimum, _text_date(due) if due else None
    return None, None, None


def _search_amount(pattern: str, text: str) -> Decimal | None:
    m = re.search(pattern, text, re.MULTILINE)
    return parse_amount(m.group(1)) if m else None


def _parse_summary(text: str) -> _Summary:
    activity = text.split("Account Activity", 1)[-1]
    installments = [
        parse_amount(m.group(1))
        for m in re.finditer(
            rf"^Installment details by .+? ({_AMOUNT}) {_AMOUNT}$", activity, re.MULTILINE
        )
    ]
    installment_total: Decimal | None = sum(installments, Decimal("0")) if installments else None
    if installment_total is None:
        # Fall back to the page 1 figure. Statements from before the first installment
        # plan print neither; the parser then sums the installment table instead.
        installment_total = _search_amount(rf"Apple Card Monthly Installments\s+({_AMOUNT})", text)
    return _Summary(
        payments=_search_amount(rf"^Total payments for this period ({_AMOUNT})", activity),
        charges=_search_amount(
            rf"^Total charges, credits, and returns for this period ({_AMOUNT})", activity
        ),
        interest=_search_amount(rf"^Total interest for this month ({_AMOUNT})", activity),
        installments=installment_total,
        remaining_financed=_search_amount(
            rf"^Total remaining financed for account ({_AMOUNT})", activity
        ),
        daily_cash=_search_amount(rf"^Total Daily Cash to account ({_AMOUNT})", activity),
    )


def _is_chrome(line: str) -> bool:
    return any(p.search(line) for p in _PAGE_CHROME) or bool(_PERIOD_RE.fullmatch(line))


def _parse_details(doc: Document, period_end: date) -> tuple[list[Transaction], list[_Plan]]:
    """Walk the Payments, Transactions and Installments tables on every page."""
    transactions: list[Transaction] = []
    plans: list[_Plan] = []
    owner: str | None = None
    mode: str | None = None
    last: Transaction | None = None
    plan: _Plan | None = None

    for page in doc.pages:
        for raw in page.lines:
            line = raw.strip()
            if not line:
                continue
            m = _OWNER_SECTION_RE.match(line)
            if m:
                owner = normalize_space(m.group(1))
                mode, last = None, None
                continue
            if line == "Date Description Amount":
                mode, last = "payment", None
                continue
            if line == "Date Description Daily Cash Amount":
                mode, last = "charge", None
                continue
            if line.startswith("Dates Description"):
                mode, last = "installment", None
                continue
            if mode is None or _is_chrome(line) or _PLACEHOLDER_RE.match(line):
                continue

            if mode == "installment":
                # Several plans can share one table; each ends at its "Total remaining" line.
                plan = _installment_line(line, owner, plan, plans)
                continue

            if line.startswith("Total "):
                mode, last = None, None
                continue

            if mode == "payment":
                m = _PAYMENT_RE.match(line)
                if m:
                    last = _transaction(m, owner, kind="payment")
                    transactions.append(last)
                    continue
            else:
                m = _TX_RE.match(line)
                if m:
                    last = _transaction(m, owner, kind=None)
                    transactions.append(last)
                    continue
                cash = _CASH_DETAIL_RE.match(line)
                if cash and last is not None:
                    label = normalize_space(cash.group("label"))
                    if cash.group("rate").startswith("-"):
                        # Daily Cash taken back after a return is charged to the card balance.
                        adjustment = Transaction(
                            date=last.date,
                            description=f"{label}: {last.description}",
                            amount=-parse_amount(cash.group("cash")),
                            kind="daily_cash_adjustment",
                            extra={
                                **_owner_extra(owner),
                                "daily_cash_rate": Decimal(cash.group("rate")),
                            },
                        )
                        transactions.append(adjustment)
                        continue
                    last.extra.setdefault("daily_cash_details", []).append(
                        {
                            "label": label,
                            "rate": Decimal(cash.group("rate")),
                            "amount": parse_amount(cash.group("cash")),
                        }
                    )
                    continue
            if last is not None:
                last.description = normalize_space(f"{last.description} {line}")
    return transactions, plans


def _transaction(m: re.Match[str], owner: str | None, kind: str | None) -> Transaction:
    month, day, year = (int(part) for part in m.group("date").split("/"))
    printed = parse_amount(m.group("amount"))
    if kind is None:
        kind = "purchase" if printed >= 0 else "refund"
    extra = _owner_extra(owner)
    groups = m.groupdict()
    if groups.get("cash"):
        extra["daily_cash_rate"] = Decimal(groups["rate"])
        extra["daily_cash"] = parse_amount(groups["cash"])
    return Transaction(
        date=date(year, month, day),
        description=normalize_space(m.group("description")),
        amount=-printed,
        kind=kind,
        extra=extra,
    )


def _owner_extra(owner: str | None) -> dict[str, Any]:
    return {"cardholder": owner} if owner else {}


def _installment_line(
    line: str, owner: str | None, plan: _Plan | None, plans: list[_Plan]
) -> _Plan | None:
    """Feed one line of an installment table; returns the open plan, or None between plans."""
    m = _TX_RE.match(line)
    if m:
        month, day, year = (int(part) for part in m.group("date").split("/"))
        plan = _Plan(
            owner=owner,
            purchase_date=date(year, month, day),
            description=normalize_space(m.group("description")),
            financed=parse_amount(m.group("amount")),
        )
        if m.group("cash"):
            plan.daily_cash_rate = Decimal(m.group("rate"))
            plan.daily_cash = parse_amount(m.group("cash"))
        plans.append(plan)
        return plan
    if plan is None:
        return None
    if found := re.match(r"^TRANSACTION #\s*(\S+)$", line):
        plan.extra["transaction_id"] = found.group(1)
    elif found := re.match(rf"^This month[\u2019']?s installment:\s*({_AMOUNT})$", line):
        plan.installment = parse_amount(found.group(1))
    elif line.startswith("Final installment"):
        if final := _TEXT_DATE_RE.search(line):
            plan.extra["final_installment"] = _text_date(final)
    elif line.startswith("This is your final installment"):
        plan.extra["final_installment"] = None
    elif found := re.match(rf"^Total (financed|payments and credits|remaining) ({_AMOUNT})$", line):
        key = _PLAN_TOTALS[found.group(1)]
        plan.extra[key] = parse_amount(found.group(2))
        if key == "remaining":
            return None
    return plan


def _installment_transactions(plans: list[_Plan], period_end: date) -> list[Transaction]:
    transactions = []
    for plan in plans:
        if not plan.installment:
            continue
        extra: dict[str, Any] = {"purchase_date": plan.purchase_date, **plan.extra}
        extra.setdefault("financed", plan.financed)
        if "final_installment" in extra and extra["final_installment"] is None:
            # "This is your final installment."
            extra["final_installment"] = period_end
        if plan.owner:
            extra["cardholder"] = plan.owner
        if plan.daily_cash is not None:
            extra["daily_cash_rate"] = plan.daily_cash_rate
            extra["daily_cash"] = plan.daily_cash
        transactions.append(
            Transaction(
                date=period_end,
                description=f"Apple Card Monthly Installment: {plan.description}",
                amount=-plan.installment,
                kind="installment",
                extra=extra,
            )
        )
    return transactions


def _parse_interest(text: str, period_end: date) -> list[Transaction]:
    m = re.search(r"^Interest Charged$(.*?)^Total interest for this month", text, re.M | re.S)
    if not m:
        return []
    transactions = []
    for line in m.group(1).splitlines():
        line = line.strip()
        found = re.match(rf"^(.+?)\s+({_AMOUNT})$", line)
        if not found or _PLACEHOLDER_RE.match(line) or found.group(1) == "—":
            continue
        printed = parse_amount(found.group(2))
        if printed == 0:
            continue
        transactions.append(
            Transaction(
                date=period_end,
                description=normalize_space(f"Interest charged {found.group(1)}"),
                amount=-printed,
                kind="interest",
            )
        )
    return transactions


def _section_sum(transactions: list[Transaction], kind: str) -> Decimal:
    return sum((t.amount for t in transactions if t.kind == kind), Decimal("0"))


def _check_sections(summary: _Summary, transactions: list[Transaction]) -> list[str]:
    """Compare each table with the Account Activity figure it should add up to."""
    checks = (
        ("payments", summary.payments, ("payment",)),
        (
            "charges, credits and returns",
            summary.charges,
            ("purchase", "refund", "daily_cash_adjustment"),
        ),
        ("installments", summary.installments, ("installment",)),
        ("interest", summary.interest, ("interest",)),
    )
    warnings = []
    for label, printed, kinds in checks:
        if printed is None:
            continue
        found = -sum((t.amount for t in transactions if t.kind in kinds), Decimal("0"))
        if abs(found - printed) > Decimal("0.01"):
            warnings.append(f"{label} total {found} differs from printed {printed}")
    return warnings


def _check_total_balance(extra: dict[str, Any], summary: _Summary) -> list[str]:
    """The total balance moves by activity plus the full price of new installment plans."""
    previous = extra.get("previous_total_balance")
    total = extra.get("total_balance")
    if previous is None or total is None or summary.payments is None or summary.charges is None:
        return []
    expected = (
        previous
        + summary.payments
        + summary.charges
        + (summary.interest or Decimal("0"))
        + extra.get("new_installment_financed", Decimal("0"))
    )
    if abs(expected - total) > Decimal("0.01"):
        return [f"total balance {total} differs from rolled-forward {expected}"]
    return []


def _parse_extra(
    page1: str,
    all_text: str,
    summary: _Summary,
    plans: list[_Plan],
    due_date: date | None,
    minimum_due: Decimal | None,
    owners: list[str],
) -> dict[str, Any]:
    extra: dict[str, Any] = {"not_printed": ["account_number"]}
    if due_date is not None:
        extra["payment_due_date"] = due_date
    if minimum_due is not None:
        extra["minimum_payment_due"] = minimum_due
    previous_total = _search_amount(rf"(?:Previous|Prior) Total Balance\s+({_AMOUNT})", page1)
    if previous_total is not None:
        extra["previous_total_balance"] = previous_total
    total = _search_amount(rf"^Total Balance\s+({_AMOUNT})", page1)
    if total is not None:
        extra["total_balance"] = total
    if summary.installments is not None:
        extra["installments_due"] = summary.installments
    if summary.remaining_financed is not None:
        extra["installments_remaining"] = summary.remaining_financed
    new_plans = [p for p in plans if p.extra.get("paid_to_date") == Decimal("0")]
    if new_plans:
        extra["new_installment_financed"] = sum((p.financed for p in new_plans), Decimal("0"))
    if summary.daily_cash is not None:
        extra["daily_cash"] = summary.daily_cash
    apr = re.search(r"Annual Percentage Rate \(APR\)\s+(\d+(?:\.\d+)?)\s*%", all_text)
    if apr:
        extra["apr"] = Decimal(apr.group(1))
    if len(owners) > 1:
        extra["co_owners"] = owners
    issuer = re.search(r"Apple Card is issued by (.+?)\.?$", page1, re.MULTILINE)
    if issuer:
        extra["issuer"] = issuer.group(1).strip()
    return extra
