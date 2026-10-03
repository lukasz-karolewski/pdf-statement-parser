from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pdf_statement_parser.document import Document
from pdf_statement_parser.models import AccountType, Statement
from pdf_statement_parser.parsers.chase.credit_card import ChaseCreditCardParser
from pdf_statement_parser.validation import reconcile

FIXTURES = Path(__file__).parents[2] / "fixtures" / "chase" / "credit_card"


def load_doc(name: str) -> Document:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return Document.from_text(text.split("\f"), name=name)


def assert_reconciles(name: str) -> Statement:
    statement = ChaseCreditCardParser().parse(load_doc(name))[0]
    assert reconcile(statement) == []
    assert not statement.warnings
    return statement


@pytest.mark.parametrize(
    "fixture",
    [
        "basic_statement.txt",
        "multipage_continuation.txt",
        "refund_statement.txt",
        "fees_interest.txt",
        "zero_activity.txt",
        "year_boundary_masked.txt",
    ],
)
def test_credit_card_fixtures_reconcile(fixture: str) -> None:
    statement = assert_reconciles(fixture)
    assert statement.bank == "Chase"
    assert statement.account_type == AccountType.CREDIT_CARD
    assert statement.account_holder == "JANE Q SAMPLE"
    assert statement.account_number
    assert statement.account_name


def test_basic_statement_metadata_and_extra_fields() -> None:
    statement = assert_reconciles("basic_statement.txt")
    assert statement.account_name == "Freedom Unlimited"
    assert statement.account_number == "4000123456789010"
    assert statement.period_start == date(2026, 3, 2)
    assert statement.period_end == date(2026, 4, 1)
    assert statement.opening_balance == Decimal("100.00")
    assert statement.closing_balance == Decimal("60.00")
    assert statement.total_deposits == Decimal("100.00")
    assert statement.total_expenses == Decimal("60.00")
    assert statement.extra["payment_due_date"] == date(2026, 4, 26)
    assert statement.extra["minimum_payment_due"] == Decimal("35.00")
    assert statement.extra["credit_limit"] == Decimal("5000.00")


def test_summary_amounts_tolerate_glued_right_column_text() -> None:
    statement = assert_reconciles("basic_statement.txt")
    assert statement.opening_balance == Decimal("100.00")
    assert statement.total_deposits == Decimal("100.00")


def test_multipage_continuation_and_order_number() -> None:
    statement = assert_reconciles("multipage_continuation.txt")
    assert statement.account_name == "Prime Visa"
    assert len(statement.transactions) == 3
    bookstore = statement.transactions[1]
    assert bookstore.extra["order_number"] == "111-2222222-3333333"
    assert "foreign currency conversion detail" in statement.transactions[2].description


def test_refund_in_purchase_section_splits_summary_net_total() -> None:
    statement = assert_reconciles("refund_statement.txt")
    assert statement.total_deposits == Decimal("25.00")
    assert statement.total_expenses == Decimal("50.00")
    assert [transaction.amount for transaction in statement.transactions] == [
        Decimal("-50.00"),
        Decimal("25.00"),
    ]
    assert statement.transactions[1].kind == "refund"


def test_fees_and_interest_sections() -> None:
    statement = assert_reconciles("fees_interest.txt")
    assert statement.account_name == "United MileagePlus"
    assert [transaction.kind for transaction in statement.transactions] == ["fee", "interest"]
    assert statement.total_expenses == Decimal("40.23")


def test_zero_activity_and_masked_account_number() -> None:
    statement = assert_reconciles("zero_activity.txt")
    assert statement.account_name == "Southwest Rapid Rewards"
    assert statement.account_number == "XXXXXXXXXXXX9050"
    assert statement.transactions == []


def test_year_boundary_dates() -> None:
    statement = assert_reconciles("year_boundary_masked.txt")
    assert statement.account_name == "Ultimate Rewards"
    assert [transaction.date for transaction in statement.transactions] == [
        date(2025, 12, 31),
        date(2026, 1, 2),
    ]


def test_detects_credit_card_and_rejects_deposit_text() -> None:
    parser = ChaseCreditCardParser()
    assert parser.detect(load_doc("basic_statement.txt")) >= 0.9
    assert parser.detect(load_doc("deposit_statement_like.txt")) == 0.0


def test_ultimate_rewards_movements() -> None:
    rewards = assert_reconciles("ultimate_rewards_rewards.txt").rewards
    assert rewards is not None
    assert rewards.program == "Ultimate Rewards"
    assert rewards.unit == "points"
    assert rewards.opening_balance == 1000
    assert rewards.closing_balance == 1100
    assert rewards.earned == 100
    assert rewards.transferred == 30
    assert rewards.redeemed == -30
    assert rewards.difference == 0


def test_prime_rewards_adjustment_and_negative_earning() -> None:
    rewards = assert_reconciles("prime_rewards.txt").rewards
    assert rewards is not None
    assert rewards.program == "Prime Visa points"
    assert rewards.earned == 20
    assert rewards.adjustments == -5
    assert rewards.closing_balance == 515
    assert rewards.difference == 0


def test_united_pass_through_rewards_with_negative_transfer() -> None:
    rewards = assert_reconciles("united_rewards.txt").rewards
    assert rewards is not None
    assert rewards.program == "MileagePlus"
    assert rewards.unit == "miles"
    assert rewards.opening_balance is None
    assert rewards.closing_balance is None
    assert rewards.earned == -80
    assert rewards.transferred == 80
    assert rewards.year_to_date == 900
    assert rewards.difference == 0


def test_united_missing_transfer_total_keeps_difference_unknown() -> None:
    rewards = assert_reconciles("united_missing_transfer.txt").rewards
    assert rewards is not None
    assert rewards.earned == 100
    assert rewards.transferred is None
    assert rewards.difference is None


def test_united_printed_zero_transfer_does_not_hide_earnings() -> None:
    rewards = assert_reconciles("united_transfer_zero.txt").rewards
    assert rewards is not None
    assert rewards.earned == 100
    assert rewards.transferred == 0
    assert rewards.difference == -100


def test_southwest_rewards_bonuses_and_garbled_balance_lines() -> None:
    rewards = assert_reconciles("southwest_rewards.txt").rewards
    assert rewards is not None
    assert rewards.program == "Rapid Rewards"
    assert rewards.opening_balance == 0
    assert rewards.earned == 100
    assert rewards.anniversary_bonus == 50
    assert rewards.welcome_bonus == 200
    assert rewards.transferred == -350
    assert rewards.closing_balance == 0
    assert rewards.difference == 0


def test_rapid_rewards_split_transfer_total_text_path() -> None:
    rewards = assert_reconciles("rapid_rewards_split_transfer.txt").rewards
    assert rewards is not None
    assert rewards.program == "Rapid Rewards"
    assert rewards.earned == 940
    assert rewards.transferred == -940
    assert rewards.opening_balance is None
    assert rewards.closing_balance is None
    assert rewards.difference == 0


def test_no_rewards_box_leaves_rewards_none() -> None:
    assert assert_reconciles("zero_activity.txt").rewards is None


def test_reward_categories_do_not_leak_to_statement_dict() -> None:
    statement = assert_reconciles("prime_rewards.txt")
    data = json.dumps(statement.to_dict())
    assert "example marketplace purchases" not in data
    assert "all other purchases" not in data
