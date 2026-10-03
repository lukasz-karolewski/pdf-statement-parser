# Chase statements

Two parsers cover Chase PDFs downloaded from chase.com (Statements and
documents). Both were developed against 585 real statements from 2019 to 2026:
8 credit card accounts and 4 deposit accounts. None of that data is in this
repository; the tests use hand-written synthetic text.

On that set, every file is detected, every account reconciles (summary totals
match the extracted transactions and balances roll forward), and no statement
carries a parser warning.

## Credit cards

Parser id: `chase-credit-card`.

Tested products: Freedom, Freedom Unlimited, Sapphire Preferred, Ink Business
Cash, Amazon Prime Visa, United MileagePlus cards including United Quest,
Southwest Rapid Rewards. Other Chase cards with the same ACCOUNT SUMMARY layout should
work; open an issue with redacted `statement-parser dump` output if one does
not.

Fields:

- `account_number` as printed. Newer statements mask it (`XXXX XXXX XXXX 1234`);
  `account_last4` works either way.
- `account_name` is the card product, `account_holder` the cardholder name.
- `period_start` and `period_end` come from "Opening/Closing Date".
- `opening_balance` is Previous Balance and `closing_balance` is New Balance.
  A positive balance is the amount owed.
- `total_deposits` is "Payment, Credits". `total_expenses` is Purchases, Cash
  Advances, Balance Transfers, Fees Charged and Interest Charged. Both come from
  the printed summary, not from the transactions, so a missed transaction fails
  `reconcile()`.
- Chase nets refunds into "Purchases". A refund listed under PURCHASE becomes a
  positive transaction with `kind="refund"`, and the same amount is added back
  to `total_expenses` and to `total_deposits`, so both sides stay checkable.
- Transaction `kind`: `credit`, `purchase`, `refund`, `fee`, `interest`,
  `cash_advance`, `balance_transfer`. Amazon order numbers go to
  `extra["order_number"]`.
- `extra`: `payment_due_date`, `statement_date`, `minimum_payment_due`,
  `credit_limit` or `credit_access_line`, `available_credit`,
  `cash_access_line`, `available_for_cash`, `past_due_amount`,
  `days_in_billing_period`, when printed.

### Rewards

| Program | Cards | Unit | Balances printed |
| --- | --- | --- | --- |
| Ultimate Rewards | Freedom, Freedom Unlimited, Sapphire, Ink | points | yes |
| Prime Visa points | Amazon Prime Visa | points | yes |
| MileagePlus | United | miles | no |
| Rapid Rewards | Southwest | points | only when negative |

Line mapping:

- "Previous points balance" is `opening_balance`; "Total points available for
  redemption" is `closing_balance`.
- Base and category earn lines ("1.5 Pts/$1 on all purchases", "5% back on ...",
  "Bonus from 4Q 5% category", "Additional miles earned on ...") are summed into
  `earned`. Category names are not exported. Returns can make `earned`
  negative.
- "New Cardmember Bonus" is `welcome_bonus`; anniversary points are
  `anniversary_bonus`.
- "Points adjusted for ..." lines are `adjustments`.
- "Points moved to/from another account" and the airline totals ("Total miles
  transferred to United", "Total Rapid Rewards transf. to Southwest") are
  `transferred`. Points sent away are negative.
- "Points redeemed this statement period" is `redeemed`, negative.
- United "Year-to-date miles earned" is `year_to_date`.

United and Southwest send the period's miles or points to the airline, so
these statements usually print no balance and the balance fields stay `None`.
For them `difference` assumes the transfer empties the card, and is `None` when
no transfer total is printed (zero-activity United months). Southwest prints a
negative balance when returns outweigh earnings; it is kept as printed and the
next statement opens with it.

Some airline totals are printed inside another column's text, so plain text
extraction reads "Thank" with the digits woven in. The parser reads those
values from character positions in the PDF. Statements built with
`Document.from_text` use the text path only.

## Checking and savings

Parser id: `chase-deposit`.

Tested products: Chase Total Checking, Chase Savings, Chase Business Complete
Checking, including trust-owned accounts.

- `account_number` is the full number as printed.
- `account_holder` is the owner block from the mailing address, without the
  street and city. Several owners are joined with ", ".
- `period_start` and `period_end` come from the "<date> through <date>" line.
- `opening_balance` and `closing_balance` are Beginning and Ending Balance.
- `total_deposits` is "Deposits and Additions" plus interest paid.
  `total_expenses` is the sum of every withdrawal, check and fee line in the
  summary.
- Transaction `balance` is filled when the statement prints a running balance;
  `reconcile()` checks it.
- Transaction `kind`: `deposit`, `withdrawal`, `electronic_withdrawal`, `card`,
  `atm`, `check`, `fee`, `interest`, `transfer`. Check numbers go to
  `extra["check_number"]`.
- `extra["summary"]` has the summary lines by name; business statements add
  `extra["transaction_counts"]` from the INSTANCES column; savings add
  `extra["interest_paid"]`.

## Consolidated personal statements

Older personal statements (headed "ASSETS", up to 2020 on the test set) mail
several accounts in one PDF with a CONSOLIDATED BALANCE SUMMARY. `parse()`
returns one `Statement` per account, primary account first. Each account's
beginning and ending balances are checked against its row in the consolidated
summary. Every statement gets a warning if the number of parsed accounts
differs from the summary rows, and an account gets one if its balances differ
from its row.

## Business checking and savings

Business statements print separate tables (DEPOSITS AND ADDITIONS, ELECTRONIC
WITHDRAWALS, CHECKS PAID, FEES) instead of one TRANSACTION DETAIL table, and no
running balance. The section decides the sign and `kind` of each row.

## Known gaps

- Not supported yet: Chase home equity, auto loan, mortgage, brokerage and CD
  statements. None were in the test set.
- Rewards `year_to_date` is only read for United, the only program in the test
  set that prints one.
- Statements must be the text PDFs Chase generates. Scanned or printed-to-PDF
  copies have no text layer and will not parse.

## Validation notes

To check the parsers against your own statements:

```bash
statement-parser validate path/to/statements -j 8
```

It prints one line per file and a summary such as:

```text
summary: 585 files, ok=585
rewards: statements=397, zero=388, nonzero=0, none=9
```

`nonzero` rewards differences are listed as notes and do not fail the run
unless you pass `--strict-rewards`. If a file fails, open an issue with
redacted `statement-parser dump` output: replace names, addresses, account
numbers and amounts before posting.
