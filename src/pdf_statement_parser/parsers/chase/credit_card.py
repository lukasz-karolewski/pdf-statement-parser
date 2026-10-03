"""Chase credit card statements.

Chase summary totals are printed by statement section, and this parser uses
those printed totals for validation. A purchase refund is printed as a negative
amount inside the purchase section, so the ACCOUNT SUMMARY reports net
purchases. The core model checks gross positive and negative transaction sums,
so the parser splits those net section totals only for opposite-signed lines:
a purchase refund adds to deposits and is added back to expenses. That keeps
the totals tied to ACCOUNT SUMMARY while making missed transactions fail
reconcile().

Rewards programs are named as "Ultimate Rewards", "Prime Visa points",
"MileagePlus", and "Rapid Rewards". Reward line signs combine the leading
statement bullet with the printed number's own sign: a plus bullet with a
negative number stays negative, and an airline transfer printed as a negative
number becomes a positive card-balance movement.

MileagePlus and Rapid Rewards send earned miles/points to the airline every
period, so these statements usually print no rewards balance; opening and
closing stay ``None``. For them ``difference`` is ``-movements`` when a transfer
total is printed (the sweep leaves nothing behind), and ``None`` when it is not.
Rapid Rewards prints a negative balance when returns outweigh earnings; that
balance is kept as printed and the next statement opens with it. When only the
closing balance is printed, the opening balance is taken as 0 for
``difference`` because the previous period's sweep emptied it; the
``opening_balance`` field itself stays ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from pdf_statement_parser.document import Document, dedupe_chars
from pdf_statement_parser.exceptions import ParseError
from pdf_statement_parser.models import AccountType, Rewards, Statement, Transaction
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
_REWARD_VALUE_RE = re.compile(r"(?<![\d/])([+-]?\d[\d,]*)(?![\d/])")
_REWARD_CROP = (265, 55, 515, 245)


@dataclass
class _RewardParse:
    rewards: Rewards | None
    warnings: list[str]


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
        account_name = _parse_account_name(page1)
        reward_parse = _parse_rewards(doc, account_name)
        warnings: list[str] = []

        if account_number is None:
            warnings.append("account number not found")
        if account_holder is None:
            warnings.append("account holder not found")
        warnings.extend(reward_parse.warnings)

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
            account_name=account_name,
            account_holder=account_holder,
            period_start=period_start,
            period_end=period_end,
            opening_balance=summary.previous_balance,
            closing_balance=summary.new_balance,
            total_deposits=total_deposits,
            total_expenses=total_expenses,
            transactions=transactions,
            rewards=reward_parse.rewards,
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


def _parse_rewards(doc: Document, account_name: str) -> _RewardParse:
    text = _extract_rewards_text(doc)
    return _parse_rewards_text(text, account_name)


def _extract_rewards_text(doc: Document) -> str:
    page = doc.pages[0]
    deduped = page.deduped
    if deduped is None:
        return page.text
    try:
        rewards_box = dedupe_chars(deduped.crop(_REWARD_CROP))
        cropped = rewards_box.extract_text(x_tolerance=1, y_tolerance=3)
    except Exception:
        return page.text
    text = cropped or page.text
    transfer_total = _positioned_rewards_transfer_total(rewards_box)
    if transfer_total is not None:
        text = _inject_rewards_transfer_total(text, transfer_total[0], transfer_total[1])
    return text


def _positioned_rewards_transfer_total(rewards_box: Any) -> tuple[str, int] | None:
    try:
        words = rewards_box.extract_words(x_tolerance=1, y_tolerance=3)
    except Exception:
        return None

    label_words = [
        word
        for word in words
        if word.get("height", 99) < 1 and _letters_only(str(word.get("text", "")))
    ]
    label = _find_rewards_transfer_label(label_words)
    if label is None:
        return None
    program, label_top = label

    digit_chars: list[dict[str, Any]] = []
    for char in rewards_box.chars:
        text = str(char.get("text", ""))
        top = float(char.get("top", 0))
        height = float(char.get("height", 99))
        if text not in "0123456789,+-" or height >= 1:
            continue
        if label_top - 1 <= top <= label_top + 30:
            digit_chars.append(char)
    if not digit_chars:
        return None

    groups: dict[int, list[dict[str, Any]]] = {}
    for char in digit_chars:
        top_key = round(float(char["top"]) * 2)
        groups.setdefault(top_key, []).append(char)

    for _, chars in sorted(groups.items(), key=lambda item: item[0]):
        if max(float(char["top"]) for char in chars) < label_top + 1:
            continue
        value = _parse_reward_chars(chars)
        if value is not None:
            return program, value
    return None


def _find_rewards_transfer_label(label_words: list[dict[str, Any]]) -> tuple[str, float] | None:
    words_by_top: dict[int, set[str]] = {}
    tops: dict[int, list[float]] = {}
    for word in label_words:
        top = float(word["top"])
        key = round(top * 2)
        words_by_top.setdefault(key, set()).add(_letters_only(str(word["text"])))
        tops.setdefault(key, []).append(top)
    for key, words in words_by_top.items():
        if {"total", "miles", "transferred", "united"} <= words:
            return "MileagePlus", min(tops[key])
        if {"total", "rapid", "rewards", "transf"} <= words:
            return "Rapid Rewards", min(tops[key])
    return None


def _parse_reward_chars(chars: list[dict[str, Any]]) -> int | None:
    text = "".join(str(char["text"]) for char in sorted(chars, key=lambda char: float(char["x0"])))
    text = text.strip()
    if not re.fullmatch(r"[+-]?\d[\d,]*", text):
        return None
    return int(text.replace(",", ""))


def _inject_rewards_transfer_total(text: str, program: str, value: int) -> str:
    if program == "MileagePlus":
        replacement = f"Total miles transferred to United {value:,}"
    else:
        replacement = f"- Total Rapid Rewards transf. to Southwest {value:,}"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _is_airline_transfer_line(_letters_only(line)):
            lines[index] = replacement
            return "\n".join(lines)
    return f"{text.rstrip()}\n{replacement}"


def _parse_rewards_text(text: str, account_name: str) -> _RewardParse:
    program = _reward_program(text, account_name)
    if program is None:
        return _RewardParse(None, [])

    unit = "miles" if program == "MileagePlus" else "points"
    rewards = Rewards(program=program, unit=unit)
    warnings: list[str] = []
    pending: str | None = None
    pending_line = ""
    seen_detail = False
    finished = False

    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        letters = _letters_only(line)
        if "accountsummary" in letters or "accountactivity" in letters:
            break
        if _is_rewards_heading(letters):
            continue

        if pending is not None:
            value = _reward_line_value(line, allow_garbled=True)
            if value is not None:
                _set_pending_reward_value(rewards, pending, value)
                if pending in {"closing_balance", "year_to_date"}:
                    finished = True
                if pending == "airline_transfer":
                    finished = True
                pending = None
                pending_line = ""
                seen_detail = True
                continue
            if _looks_like_reward_line(line, letters):
                warnings.append(f"unrecognised rewards line after {pending_line}")
            pending = None
            pending_line = ""

        if finished and not _is_ytd_line(letters):
            continue

        if _is_previous_rewards_balance_line(letters):
            value = _reward_line_value(line, allow_garbled=True)
            if value is None:
                pending = "opening_balance"
                pending_line = "opening balance"
            else:
                rewards.opening_balance = value
            seen_detail = True
            continue

        if program != "MileagePlus" and _is_closing_rewards_balance_line(letters):
            value = _reward_line_value(line, allow_garbled=True)
            if value is None:
                pending = "closing_balance"
                pending_line = "closing balance"
            else:
                rewards.closing_balance = value
                finished = True
            seen_detail = True
            continue

        if _is_ytd_line(letters):
            value = _reward_line_value(line, allow_garbled=True)
            if value is None:
                pending = "year_to_date"
                pending_line = "year-to-date"
            else:
                rewards.year_to_date = value
                finished = True
            seen_detail = True
            continue

        if _is_airline_transfer_line(letters):
            value = _reward_line_value(line, allow_garbled=False)
            if value is None:
                pending = "airline_transfer"
                pending_line = "airline transfer"
            else:
                _add_reward_value(rewards, "transferred", _airline_transfer_movement(line, value))
                if program == "MileagePlus":
                    finished = True
                elif "rapidrewards" in letters and (
                    value == 0 or rewards.opening_balance is not None
                ):
                    pending = "closing_balance"
                    pending_line = "closing balance"
                elif program == "Rapid Rewards":
                    finished = True
            seen_detail = True
            continue

        if program == "MileagePlus" and seen_detail and _leading_reward_bullet(line) is None:
            break

        value = _signed_reward_line_value(line)
        if value is None:
            if _looks_like_reward_line(line, letters) and seen_detail:
                warnings.append("unrecognised rewards line")
            continue

        if _is_redeemed_line(letters):
            _add_reward_value(rewards, "redeemed", value)
        elif _is_transfer_line(letters):
            _add_reward_value(rewards, "transferred", value)
        elif _is_adjustment_line(letters):
            _add_reward_value(rewards, "adjustments", value)
        elif _is_welcome_bonus_line(letters):
            _add_reward_value(rewards, "welcome_bonus", value)
        elif _is_anniversary_bonus_line(letters):
            _add_reward_value(rewards, "anniversary_bonus", value)
        elif _is_earned_line(line, letters):
            _add_reward_value(rewards, "earned", value)
        elif _is_other_bonus_line(letters):
            _add_reward_value(rewards, "other_bonus", value)
        elif _looks_like_reward_line(line, letters) and seen_detail:
            warnings.append("unrecognised rewards line")
            continue
        else:
            continue
        seen_detail = True

    if pending is not None:
        warnings.append(f"unrecognised rewards line after {pending_line}")

    rewards.difference = _reward_difference(rewards)
    if not seen_detail:
        return _RewardParse(None, [])
    return _RewardParse(rewards, warnings)


def _reward_program(text: str, account_name: str) -> str | None:
    upper = text.upper()
    if "MILEAGEPLUS" in upper or "UNITED" in account_name.upper():
        return "MileagePlus"
    if "RAPID REWARDS" in upper or "SOUTHWEST" in account_name.upper():
        return "Rapid Rewards"
    if "PRIME VISA" in upper or "AMAZON" in upper or account_name == "Prime Visa":
        return "Prime Visa points"
    if (
        "ULTIMATE REWARDS" in upper
        or "REWARDS SUMMARY" in upper
        or account_name
        in {
            "Freedom",
            "Freedom Unlimited",
            "Sapphire",
            "Sapphire Preferred",
            "Sapphire Reserve",
            "Ink Business Cash",
            "Ultimate Rewards",
        }
    ):
        return "Ultimate Rewards"
    return None


def _reward_difference(rewards: Rewards) -> int | None:
    if rewards.opening_balance is not None and rewards.closing_balance is not None:
        return rewards.closing_balance - rewards.opening_balance - rewards.movements
    if rewards.opening_balance is None and rewards.closing_balance is not None:
        return rewards.closing_balance - rewards.movements
    if (
        rewards.transferred is not None
        and rewards.opening_balance is None
        and rewards.closing_balance is None
    ):
        return -rewards.movements
    return None


def _set_pending_reward_value(rewards: Rewards, pending: str, value: int) -> None:
    if pending == "opening_balance":
        rewards.opening_balance = value
    elif pending == "closing_balance":
        rewards.closing_balance = value
    elif pending == "year_to_date":
        rewards.year_to_date = value
    elif pending == "airline_transfer":
        _add_reward_value(rewards, "transferred", -value)


def _add_reward_value(rewards: Rewards, field: str, value: int) -> None:
    current = getattr(rewards, field)
    if current is None:
        setattr(rewards, field, value)
    else:
        setattr(rewards, field, current + value)


def _reward_line_value(line: str, *, allow_garbled: bool) -> int | None:
    if allow_garbled and re.search(r"[A-Za-z]\d|\d[A-Za-z]", line):
        digits = "".join(ch for ch in line if ch.isdigit())
        if digits:
            sign = -1 if re.search(r"-\s*[A-Za-z]*\d", line) else 1
            return sign * int(digits)
    matches = list(_REWARD_VALUE_RE.finditer(line))
    if matches:
        return int(matches[-1].group(1).replace(",", ""))
    if not allow_garbled:
        return None
    digits = "".join(ch for ch in line if ch.isdigit())
    if not digits:
        return None
    sign = -1 if re.search(r"-\s*[A-Za-z]*\d", line) else 1
    return sign * int(digits)


def _plain_reward_line_value(line: str) -> int | None:
    text = line.strip()
    if not re.fullmatch(r"[+-]?\d[\d,]*", text):
        return None
    return int(text.replace(",", ""))


def _signed_reward_line_value(line: str) -> int | None:
    value = _reward_line_value(line, allow_garbled=False)
    if value is None:
        return None
    amount_sign = -1 if value < 0 else 1
    bullet = _leading_reward_bullet(line)
    if bullet == "-":
        return -abs(value) * amount_sign
    if bullet == "+":
        return abs(value) * amount_sign
    return value


def _airline_transfer_movement(line: str, value: int) -> int:
    bullet = _leading_reward_bullet(line)
    if bullet is not None:
        signed = _signed_reward_line_value(line)
        return signed if signed is not None else 0
    return -value


def _leading_reward_bullet(line: str) -> str | None:
    stripped = line.lstrip()
    if stripped.startswith("+"):
        return "+"
    if stripped.startswith("-"):
        return "-"
    return None


def _is_rewards_heading(letters: str) -> bool:
    return letters in {
        "summary",
        "rewardssummary",
        "milessummary",
        "yourprimevisapoints",
        "ultimatewards",
        "ultimaterewards",
        "chaseultimaterewards",
        "unitedmileageplusaward",
        "southwestrapid",
        "rewardscreditcard",
    }


def _is_previous_rewards_balance_line(letters: str) -> bool:
    return (
        "previouspointsbalance" in letters
        or "previousmonthsbalance" in letters
        or (
            "evi" in letters
            and "ous" in letters
            and (
                "points" in letters or "month" in letters or ("mo" in letters and "nth" in letters)
            )
            and ("balance" in letters or "lance" in letters)
        )
    )


def _is_closing_rewards_balance_line(letters: str) -> bool:
    return (
        "totalpointsavailable" in letters
        or "rewardspointsbalance" in letters
        or ("redemption" in letters and "total" not in letters)
    )


def _is_ytd_line(letters: str) -> bool:
    return "yeartodate" in letters


def _is_airline_transfer_line(letters: str) -> bool:
    return "totalmilestransferred" in letters or "totalrapidrewardstransf" in letters


def _is_transfer_line(letters: str) -> bool:
    return "pointsmovedfromanotheraccount" in letters or "pointsmovedtoanotheraccount" in letters


def _is_redeemed_line(letters: str) -> bool:
    return "redeemed" in letters


def _is_adjustment_line(letters: str) -> bool:
    return "adjust" in letters


def _is_welcome_bonus_line(letters: str) -> bool:
    return "newcardmember" in letters or "welcome" in letters


def _is_anniversary_bonus_line(letters: str) -> bool:
    return "anniversary" in letters or ("annual" in letters and "bonus" in letters)


def _is_other_bonus_line(letters: str) -> bool:
    return "bonus" in letters or "promotional" in letters or "referral" in letters


def _is_earned_line(line: str, letters: str) -> bool:
    lower = line.lower()
    if "%" in line:
        return True
    if re.search(r"\b\d+(?:\.\d+)?x\b", lower):
        return True
    if "bonus" in letters and (
        "category" in letters or "purchase" in letters or "%" in line or "back" in letters
    ):
        return True
    return any(
        token in lower
        for token in (
            "earned",
            "purchases",
            "purchase",
            " pts ",
            " pt ",
            "point per",
            "points per",
            "pt per",
            "points earned",
            "mile per",
            "miles earned",
            "addl miles",
            "back",
            "x pts",
            "x points",
            "x miles",
        )
    )


def _looks_like_reward_line(line: str, letters: str) -> bool:
    return _leading_reward_bullet(line) is not None or any(
        token in letters
        for token in (
            "point",
            "mile",
            "reward",
            "redeem",
            "bonus",
            "transf",
            "earned",
            "adjust",
            "balance",
        )
    )


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
