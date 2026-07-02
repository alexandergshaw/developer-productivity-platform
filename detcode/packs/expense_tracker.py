"""Domain pack: expense tracker.

Fully deterministic domain: parse CSV transactions, categorize them with a
fixed keyword-rule table, and report monthly totals per category. Amounts are
integer cents throughout — no float accumulation drift.
"""
from __future__ import annotations

from . import Pack

_TRANSACTIONS = '''
"""Parse transactions from CSV text: date,description,amount

The first line may be a header (detected, skipped). Dates are YYYY-MM-DD.
Amounts are parsed to integer cents — no float accumulation drift.
"""
import re

_DATE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}$")
_AMOUNT = re.compile(r"^-?\\d+(\\.\\d{1,2})?$")


def parse_amount(text):
    """Parse "12.34" / "-7" into integer cents."""
    text = text.strip()
    if not _AMOUNT.match(text):
        raise ValueError(f"bad amount: {text!r}")
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("-")
    whole, _, frac = text.partition(".")
    return sign * (int(whole) * 100 + int(frac.ljust(2, "0") or 0))


def parse_transactions(text):
    """Parse CSV text into transaction dicts, in file order.

    Malformed lines raise ValueError with their line number — loudly, never
    silently skipped.
    """
    rows = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            raise ValueError(f"line {lineno}: expected date,description,amount")
        date, amount_text = parts[0], parts[-1]
        description = ",".join(parts[1:-1])
        if lineno == 1 and not _DATE.match(date):
            continue  # header row
        if not _DATE.match(date):
            raise ValueError(f"line {lineno}: bad date {date!r} (want YYYY-MM-DD)")
        rows.append(
            {"date": date, "description": description, "amount": parse_amount(amount_text)}
        )
    return rows
'''

_CATEGORIZE = '''
"""Categorize transactions with a fixed keyword-rule table.

Rules are checked in table order and keywords match as lowercase substrings
of the description; the first hit wins. Nothing statistical — edit the table
to change behavior.
"""

RULES = (
    ("income", ("payroll", "salary", "paycheck", "refund")),
    ("groceries", ("grocery", "kroger", "aldi", "walmart", "whole foods", "trader joe")),
    ("dining", ("restaurant", "cafe", "coffee", "starbucks", "pizza", "doordash", "grubhub")),
    ("transport", ("uber", "lyft", "shell", "chevron", "gas", "parking", "transit", "metro")),
    ("housing", ("rent", "mortgage", "hoa")),
    ("utilities", ("electric", "water", "internet", "comcast", "verizon", "phone")),
    ("entertainment", ("netflix", "spotify", "hulu", "cinema", "steam")),
)


def categorize(description):
    """The first rule whose keyword appears in the description; else "other"."""
    lowered = description.lower()
    for category, keywords in RULES:
        if any(keyword in lowered for keyword in keywords):
            return category
    return "other"
'''

_REPORT = '''
"""Monthly per-category totals and a printable report."""
from .categorize import categorize


def fmt_cents(cents):
    """Integer cents -> "$12.34" (deterministic, sign-aware)."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100}.{cents % 100:02d}"


def monthly_summary(transactions):
    """{month: {category: total_cents}}, months and categories sorted."""
    summary = {}
    for tx in transactions:
        month = tx["date"][:7]
        category = categorize(tx["description"])
        summary.setdefault(month, {})
        summary[month][category] = summary[month].get(category, 0) + tx["amount"]
    return {
        month: dict(sorted(categories.items()))
        for month, categories in sorted(summary.items())
    }


def format_report(transactions):
    """A printable monthly report with per-category and total lines."""
    lines = []
    for month, categories in monthly_summary(transactions).items():
        lines.append(month)
        for category, cents in categories.items():
            lines.append(f"  {category:<14}{fmt_cents(cents):>12}")
        lines.append(f"  {'total':<14}{fmt_cents(sum(categories.values())):>12}")
        lines.append("")
    return "\\n".join(lines).rstrip("\\n")
'''

_CLI = '''
"""Command-line interface: __PKG__ report transactions.csv"""
import argparse

from .report import format_report
from .transactions import parse_transactions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="__PKG__", description="Deterministic expense tracking from CSV transactions."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="monthly totals per category")
    report.add_argument("csv", help="path to transactions CSV (date,description,amount)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    with open(args.csv, "r", encoding="utf-8-sig") as fh:
        print(format_report(parse_transactions(fh.read())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_MAIN = '''
"""Enables python -m __PKG__."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
'''

_TESTS = '''
"""Tests for the generated __PKG__ project."""
import unittest

from __PKG__.categorize import categorize
from __PKG__.report import fmt_cents, format_report, monthly_summary
from __PKG__.transactions import parse_amount, parse_transactions

CSV = """date,description,amount
2026-05-01,Kroger #42,-82.17
2026-05-03,Starbucks,-6.50
2026-05-15,Payroll ACME,2500.00
2026-06-01,Uber trip,-14.25
"""


class ParseTests(unittest.TestCase):
    def test_amount_to_cents(self):
        self.assertEqual(parse_amount("12.34"), 1234)
        self.assertEqual(parse_amount("-7"), -700)
        self.assertEqual(parse_amount("0.5"), 50)

    def test_header_skipped_and_rows_parsed(self):
        rows = parse_transactions(CSV)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["amount"], -8217)

    def test_malformed_line_is_loud(self):
        with self.assertRaises(ValueError):
            parse_transactions("2026-05-01,thing,not-a-number")


class CategorizeTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(categorize("KROGER #42"), "groceries")
        self.assertEqual(categorize("Starbucks"), "dining")
        self.assertEqual(categorize("Payroll ACME"), "income")
        self.assertEqual(categorize("mystery merchant"), "other")


class ReportTests(unittest.TestCase):
    def test_monthly_summary(self):
        summary = monthly_summary(parse_transactions(CSV))
        self.assertEqual(summary["2026-05"]["groceries"], -8217)
        self.assertEqual(summary["2026-05"]["income"], 250000)
        self.assertEqual(summary["2026-06"]["transport"], -1425)

    def test_fmt_cents(self):
        self.assertEqual(fmt_cents(-8217), "-$82.17")
        self.assertEqual(fmt_cents(50), "$0.50")

    def test_report_deterministic(self):
        rows = parse_transactions(CSV)
        outputs = {format_report(rows) for _ in range(5)}
        self.assertEqual(len(outputs), 1)
        self.assertIn("2026-05", format_report(rows))


if __name__ == "__main__":
    unittest.main()
'''


def _files() -> dict[str, str]:
    return {
        "__PKG__/__init__.py": '"""__PKG__ (generated by detcode)."""\n',
        "__PKG__/transactions.py": _TRANSACTIONS.lstrip("\n"),
        "__PKG__/categorize.py": _CATEGORIZE.lstrip("\n"),
        "__PKG__/report.py": _REPORT.lstrip("\n"),
        "__PKG__/cli.py": _CLI.lstrip("\n"),
        "__PKG__/__main__.py": _MAIN.lstrip("\n"),
        "tests/__init__.py": "",
        "tests/test___PKG__.py": _TESTS.lstrip("\n"),
    }


PACK = Pack(
    key="expense-tracker",
    title="Expense tracker",
    default_slug="expense_tracker",
    keywords=frozenset(
        ["expense", "expenses", "budget", "budgeting", "spending", "transactions", "finances"]
    ),
    description=(
        "a working expense tracker: CSV transaction parsing, keyword-rule "
        "categorization, and monthly per-category reports in integer cents, "
        "with tests"
    ),
    files=_files,
)
