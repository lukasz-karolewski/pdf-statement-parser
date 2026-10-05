# Apple Card statements

Parser id: `apple-card`.

The parser reads the monthly statement PDFs exported from Wallet or
card.apple.com. It was developed against 60 real statements from October 2021
to September 2026, all from one co-owned account. None of that data is in this
repository; the tests use hand-written synthetic text.

On that set, every file is detected, every statement reconciles, the monthly
balance carries over from one statement to the next, and no statement carries
a parser warning.

## Balances

Apple Card prints two balances:

- The **monthly balance** ("Your March Balance") is the amount due this month.
  It includes this month's Apple Card Monthly Installments.
- The **total balance** also includes every installment not yet billed.

The parser uses the monthly balance:

- `opening_balance` is "Previous Monthly Balance" ("Prior Monthly Balance" on
  older statements).
- `closing_balance` is "Your <Month> Balance".
- Each active installment plan adds one transaction with `kind="installment"`
  for this month's installment, dated on the last day of the period.

A new installment purchase therefore shows up month by month as installments,
not as one purchase for the full price. The full price is in
`extra["new_installment_financed"]` for the month the plan starts.

## Fields

- `bank` is `Apple` and `account_name` is `Apple Card`.
- `account_number` is `None`, because the statement does not print one.
  `extra["not_printed"]` is `["account_number"]`, so `reconcile()` and
  `validate` do not report it as missing.
- `account_holder` is the owner, or co-owners separated by commas, as printed
  in the header.
- `period_start` and `period_end` come from the header ("Mar 1 — Mar 31, 2025").
- `total_deposits` is total payments plus returns. `total_expenses` is charges,
  installments and interest. Both come from the printed Account Activity
  figures. The statement nets returns into "charges, credits, and returns", so
  the parser adds returns back to expenses and also counts them as deposits.
  That way a missed transaction still fails `reconcile()`.

Transaction `kind`:

| Kind | Source |
| --- | --- |
| `payment` | Payments table |
| `purchase` | Transactions table, positive amount |
| `refund` | Transactions table, negative amount (for example `(RETURN)`) |
| `daily_cash_adjustment` | "Daily Cash Adjustment" line under a return. Apple charges back the Daily Cash it paid on the returned purchase. |
| `installment` | One per active Apple Card Monthly Installments plan |
| `interest` | Interest Charged lines. None of the 60 statements has interest, so this has not been seen on a real statement. |

Transaction `extra`:

- `cardholder`: the co-owner the table is printed under.
- `daily_cash_rate` and `daily_cash`: the Daily Cash percentage and amount.
- `daily_cash_details`: extra Daily Cash lines under a purchase, such as
  "Promo Daily Cash 1% $0.44".
- For installments: `purchase_date`, `transaction_id`, `final_installment`,
  `financed`, `paid_to_date` and `remaining`. In the month a plan starts,
  `daily_cash` is the Daily Cash earned on the full purchase.

Statement `extra`: `payment_due_date`, `minimum_payment_due`,
`previous_total_balance`, `total_balance`, `installments_due`,
`installments_remaining`, `new_installment_financed`, `daily_cash`, `apr`,
`co_owners` and `issuer`, when printed.

## Daily Cash

Daily Cash is paid to Apple Cash or Apple Savings, not kept as a points balance
on the card. It is reported in `extra` and `Statement.rewards` is `None`.

## Checks

The parser adds a warning when:

- the payments, charges, installments or interest tables do not add up to
  their Account Activity totals, or
- the total balance does not roll forward:
  previous total balance + payments + charges + interest + newly financed
  installments must equal the total balance.
