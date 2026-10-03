"""Run every parser over a folder of real statements and report what reconciles.

Real statements are private and never committed. Point this at your own:

    python scripts/validate_corpus.py statements/ -j 8 --report corpus-report.json

A file passes when it is detected, parses without error, and every account in
it reconciles (summary totals match transactions, balances roll forward).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from statement_parser import parse, reconcile
from statement_parser.exceptions import UnsupportedStatementError

REQUIRED = (
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


def check(path: str) -> dict:
    row: dict = {"file": path}
    try:
        result = parse(path)
    except UnsupportedStatementError as exc:
        return {**row, "status": "undetected", "error": str(exc)}
    except Exception as exc:
        return {**row, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    row["parser"] = result.parser_id
    accounts = []
    ok = bool(result.statements)
    for st in result.statements:
        issues = reconcile(st)
        missing = [f for f in REQUIRED if getattr(st, f) is None]
        ok = ok and not issues and not missing
        accounts.append({
            "account_last4": st.account_last4,
            "account_type": st.account_type.value,
            "account_name": st.account_name,
            "period": [str(st.period_start), str(st.period_end)],
            "transactions": len(st.transactions),
            "missing": missing,
            "issues": [f"{i.check}: {i.message}" for i in issues],
            "warnings": st.warnings,
        })
    row["accounts"] = accounts
    row["status"] = "ok" if ok else "mismatch"
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--report", type=Path, help="write per-file JSON results here")
    ap.add_argument("--filter", default="", help="only files whose path contains this")
    ap.add_argument("--show", type=int, default=15, help="failures to print")
    args = ap.parse_args()

    files = sorted(str(p) for p in args.root.rglob("*.pdf") if args.filter in str(p))
    with ProcessPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(check, files, chunksize=4))

    status = Counter(r["status"] for r in rows)
    by_parser = Counter((r.get("parser"), r["status"]) for r in rows)
    print(f"{len(rows)} files: " + ", ".join(f"{k}={v}" for k, v in sorted(status.items())))
    for (parser, st), n in sorted(by_parser.items(), key=lambda x: (str(x[0][0]), x[0][1])):
        print(f"  {parser!s:22} {st:10} {n}")

    issue_kinds: Counter[str] = Counter()
    for r in rows:
        for a in r.get("accounts", []):
            issue_kinds.update(i.split(":")[0] for i in a["issues"])
            issue_kinds.update(f"missing:{m}" for m in a["missing"])
    if issue_kinds:
        print("issue counts:")
        for k, n in issue_kinds.most_common():
            print(f"  {k:30} {n}")

    bad = [r for r in rows if r["status"] != "ok"]
    for r in bad[: args.show]:
        detail = r.get("error") or "; ".join(
            f"[{a['account_last4']}] " + ", ".join(a["issues"] + [f"missing {m}" for m in a["missing"]])
            for a in r["accounts"]
        )
        print(f"FAIL {r['file']}: {detail[:300]}")

    if args.report:
        args.report.write_text(json.dumps(rows, indent=2))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
