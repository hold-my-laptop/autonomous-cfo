"""Import a German online-banking CSV statement (Pizzeria Amalfi sample format)."""

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

from api.dates import parse_german_date
from api.domain.models import Category, Transaction
from api.money import convert, parse_german_amount

ACCOUNT_CURRENCY = "EUR"
_ID_NAMESPACE = UUID("6f1c2d3e-4b5a-4c6d-8e7f-90a1b2c3d4e5")

REQUIRED_COLUMNS = (
    "Account Number",
    "Booking Date",
    "Value Date",
    "Transaction Type",
    "Payment Reference",
    "Beneficiary/Payer",
    "Counterparty Account",
    "Amount",
    "Currency",
)

_TYPE_CATEGORIES = {
    "CARD SETTLEMENT (DEBIT)": Category.CARD_REVENUE,
    "CREDIT CARD PAYOUT": Category.CARD_REVENUE,
    "GIRO CARD PAYMENT": Category.VARIABLE_COST,
    "SUPPLIER PAYMENT": Category.VARIABLE_COST,
    "RENT PAYMENT": Category.RENT,
    "SALARY PAYMENT": Category.PAYROLL,
    "TAX OFFICE TRANSFER": Category.TAX,
    "INTERNATIONAL WIRE TRANSFER": Category.PARTNER_TRANSFER,
}

_DIRECT_DEBIT_KEYWORDS = (
    ("tax office", Category.TAX),
    ("finanzamt", Category.TAX),
    ("krankenkasse", Category.SOCIAL_SECURITY),
    ("social", Category.SOCIAL_SECURITY),
    ("energy", Category.UTILITIES),
    ("utilities", Category.UTILITIES),
)


class StatementImportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StatementImport:
    account: str
    transactions: list[Transaction]
    date_from: date
    date_to: date
    possible_duplicates: int


def classify(transaction_type: str, counterparty: str) -> Category:
    category = _TYPE_CATEGORIES.get(transaction_type.strip().upper())
    if category is not None:
        return category
    if transaction_type.strip().upper() == "DIRECT DEBIT":
        name = counterparty.lower()
        for keyword, keyword_category in _DIRECT_DEBIT_KEYWORDS:
            if keyword in name:
                return keyword_category
    return Category.OTHER


def _fx_rate(reference: str, currency: str) -> str:
    match = re.search(rf"@\s*([0-9]+(?:\.[0-9]+)?)\s*{ACCOUNT_CURRENCY}/{currency}", reference)
    if match is None:
        raise StatementImportError(
            f"{currency} amount without a {ACCOUNT_CURRENCY}/{currency} rate in the reference"
        )
    return match.group(1)


def _parse_row(row: dict[str, str], line: int) -> Transaction:
    try:
        currency = row["Currency"].strip().upper()
        amount = parse_german_amount(row["Amount"])
        reference = row["Payment Reference"].strip()
        original_amount = original_currency = fx_rate = None
        if currency != ACCOUNT_CURRENCY:
            fx_rate = _fx_rate(reference, currency)
            original_amount, original_currency = amount, currency
            amount = convert(amount, fx_rate)
        counterparty = row["Beneficiary/Payer"].strip()
        return Transaction(
            id=uuid5(_ID_NAMESPACE, f"{line}|{'|'.join(row.values())}"),
            account=row["Account Number"].strip(),
            booking_date=parse_german_date(row["Booking Date"]),
            value_date=parse_german_date(row["Value Date"]),
            type=row["Transaction Type"].strip(),
            counterparty=counterparty,
            counterparty_account=row["Counterparty Account"].strip(),
            reference=reference,
            amount=amount,
            category=classify(row["Transaction Type"], counterparty),
            original_amount=original_amount,
            original_currency=original_currency,
            fx_rate=fx_rate,
        )
    except (ValueError, KeyError) as exc:
        raise StatementImportError(f"line {line}: {exc}") from exc


def parse_statement(text: str) -> StatementImport:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";")
    missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
    if missing:
        raise StatementImportError(f"missing columns: {', '.join(missing)}")

    rows = [(line, row) for line, row in enumerate(reader, start=2)]
    if not rows:
        raise StatementImportError("statement has no transactions")

    parsed = [(line, _parse_row(row, line)) for line, row in rows]
    accounts = {transaction.account for _, transaction in parsed}
    if len(accounts) != 1:
        raise StatementImportError(f"expected one account, found {len(accounts)}")

    transactions = [t for _, t in sorted(parsed, key=lambda item: (item[1].booking_date, -item[0]))]
    identical = Counter(tuple(row.values()) for _, row in rows)
    return StatementImport(
        account=accounts.pop(),
        transactions=transactions,
        date_from=transactions[0].booking_date,
        date_to=transactions[-1].booking_date,
        possible_duplicates=sum(count - 1 for count in identical.values()),
    )


def load_statement(path: Path) -> StatementImport:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise StatementImportError(f"cannot read statement {path}: {exc}") from exc
    return parse_statement(text)
