"""Small helpers shared by parsers: amounts, dates, whitespace."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_AMOUNT_RE = re.compile(r"^\(?([+-]?)\s*\$?\s*([+-]?)([\d,]*\.?\d+)\)?(-|CR)?$", re.IGNORECASE)

#: Regex fragment matching a printed amount such as ``-$1,234.56`` or ``12.00``.
AMOUNT_PATTERN = r"[-+]?\$?[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"


def parse_amount(text: str) -> Decimal:
    """Parse a printed amount.

    Handles ``$``, thousands separators, a leading or trailing minus,
    parentheses and a trailing ``CR`` as negative markers.

    >>> parse_amount("-$1,164.93")
    Decimal('-1164.93')
    >>> parse_amount("(12.00)")
    Decimal('-12.00')
    """
    raw = text.strip().replace(" ", "")
    m = _AMOUNT_RE.match(raw)
    if not m:
        raise ValueError(f"not an amount: {text!r}")
    sign1, sign2, digits, suffix = m.groups()
    try:
        value = Decimal(digits.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {text!r}") from exc
    negative = "-" in (sign1, sign2) or bool(suffix) or (raw.startswith("(") and raw.endswith(")"))
    return -value if negative else value


def parse_date(text: str, formats: tuple[str, ...] = ("%m/%d/%y", "%m/%d/%Y", "%B %d, %Y")) -> date:
    text = " ".join(text.split())
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {text!r}")


def resolve_month_day(
    month: int, day: int, period_start: date | None, period_end: date
) -> date:
    """Turn a year-less ``MM/DD`` into a full date inside (or near) a period.

    Statements list ``12/30`` and ``01/02`` side by side when the period
    crosses New Year. Pick the year that puts the date closest to the period.
    """
    candidates = []
    for year in (period_end.year - 1, period_end.year, period_end.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:  # Feb 29 in a non-leap year
            continue
    if not candidates:
        raise ValueError(f"invalid month/day {month}/{day}")
    start = period_start or period_end

    def distance(d: date) -> int:
        if start <= d <= period_end:
            return 0
        return min(abs((d - start).days), abs((d - period_end).days))

    return min(candidates, key=distance)


def normalize_space(text: str) -> str:
    return " ".join(text.split())
