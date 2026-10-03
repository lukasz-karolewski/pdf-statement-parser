from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pdf_statement_parser.utils import parse_amount, parse_date, resolve_month_day


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,234.56", Decimal("1234.56")),
        ("-$1,234.56", Decimal("-1234.56")),
        ("$-1,234.56", Decimal("-1234.56")),
        ("(12.00)", Decimal("-12.00")),
        ("12.00CR", Decimal("-12.00")),
    ],
)
def test_parse_amount(text: str, expected: Decimal) -> None:
    assert parse_amount(text) == expected


def test_parse_amount_rejects_bad_text() -> None:
    with pytest.raises(ValueError, match="not an amount"):
        parse_amount("n/a")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("01/02/26", date(2026, 1, 2)),
        ("01/02/2026", date(2026, 1, 2)),
        ("January 2, 2026", date(2026, 1, 2)),
    ],
)
def test_parse_date(text: str, expected: date) -> None:
    assert parse_date(text) == expected


def test_resolve_month_day_crosses_year_boundary() -> None:
    assert resolve_month_day(12, 31, date(2025, 12, 15), date(2026, 1, 14)) == date(2025, 12, 31)
    assert resolve_month_day(1, 2, date(2025, 12, 15), date(2026, 1, 14)) == date(2026, 1, 2)


def test_resolve_month_day_handles_feb_29() -> None:
    assert resolve_month_day(2, 29, date(2024, 2, 1), date(2024, 3, 1)) == date(2024, 2, 29)


def test_resolve_month_day_rejects_invalid_day() -> None:
    with pytest.raises(ValueError, match="invalid month/day"):
        resolve_month_day(2, 30, date(2026, 2, 1), date(2026, 2, 28))
