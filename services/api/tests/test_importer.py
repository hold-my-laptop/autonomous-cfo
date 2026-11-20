from datetime import date

import pytest

from api.config import get_settings
from api.domain.balances import balance_before, daily_closing_balances
from api.domain.importer import (
    StatementImportError,
    classify,
    load_statement,
    parse_statement,
)
from api.domain.models import Category

HEADER = (
    '"Account Number";"Booking Date";"Value Date";"Transaction Type";"Payment Reference";'
    '"Beneficiary/Payer";"Counterparty Account";"Bank Code";"Amount";"Currency";"Info"\n'
)
ACCOUNT = "DE89370400440532013000"


def row(day: str, kind: str, reference: str, counterparty: str, amount: str, currency="EUR"):
    return (
        f'"{ACCOUNT}";"{day}";"{day}";"{kind}";"{reference}";"{counterparty}";'
        f'"DE00";"XXX";"{amount}";"{currency}";"Transaction booked"\n'
    )


SMALL = HEADER + "".join(
    [
        row("15.12.25", "INTERNATIONAL WIRE TRANSFER",
            "Silent Partnership Capital Tranche 12/12 (4166.67 CHF @ 1.047 EUR/CHF)",
            "Luigi Amalfi", "-4166,67", "CHF"),
        row("10.12.25", "DIRECT DEBIT", "Utilities", "City Energy & Utilities AG", "-650,00"),
        row("10.12.25", "DIRECT DEBIT", "Income tax", "Tax Office Frankfurt am Main", "-1150,00"),
        row("27.11.25", "DIRECT DEBIT", "Contributions", "Techniker Krankenkasse", "-2650,00"),
        row("01.11.25", "CREDIT CARD PAYOUT", "Global Pay", "Commerzbank Global Pay", "1149,36"),
    ]
)  # fmt: skip


def test_parse_small_statement() -> None:
    result = parse_statement(SMALL)

    assert result.account == ACCOUNT
    assert (result.date_from, result.date_to) == (date(2025, 11, 1), date(2025, 12, 15))
    assert [t.booking_date for t in result.transactions] == sorted(
        t.booking_date for t in result.transactions
    )
    by_ref = {t.reference.split()[0]: t for t in result.transactions}
    assert by_ref["Utilities"].category is Category.UTILITIES
    assert by_ref["Income"].category is Category.TAX
    assert by_ref["Contributions"].category is Category.SOCIAL_SECURITY
    assert by_ref["Global"].category is Category.CARD_REVENUE


def test_chf_row_is_converted_and_keeps_original() -> None:
    chf = next(t for t in parse_statement(SMALL).transactions if t.original_currency)

    assert chf.amount == -436250  # 4 166,67 CHF × 1,047 = 4 362,50 €
    assert (chf.original_amount, chf.original_currency, chf.fx_rate) == (-416667, "CHF", "1.047")
    assert chf.category is Category.PARTNER_TRANSFER


def test_ids_are_stable() -> None:
    first = [t.id for t in parse_statement(SMALL).transactions]
    assert first == [t.id for t in parse_statement(SMALL).transactions]
    assert len(set(first)) == len(first)


def test_bom_is_ignored() -> None:
    assert len(parse_statement("﻿" + SMALL).transactions) == 5


@pytest.mark.parametrize(
    ("kind", "counterparty", "category"),
    [
        ("SALARY PAYMENT", "Rosa Pavese", Category.PAYROLL),
        ("RENT PAYMENT", "Main Street Real Estate GmbH", Category.RENT),
        ("GIRO CARD PAYMENT", "Central Wholesale Market", Category.VARIABLE_COST),
        ("TAX OFFICE TRANSFER", "Tax Office Frankfurt am Main", Category.TAX),
        ("DIRECT DEBIT", "Unknown GmbH", Category.OTHER),
        ("STANDING ORDER", "Someone", Category.OTHER),
    ],
)
def test_classify(kind: str, counterparty: str, category: Category) -> None:
    assert classify(kind, counterparty) is category


def test_missing_columns_are_rejected() -> None:
    with pytest.raises(StatementImportError, match="missing columns"):
        parse_statement('"Account Number";"Amount"\n"X";"1,00"\n')


def test_empty_statement_is_rejected() -> None:
    with pytest.raises(StatementImportError, match="no transactions"):
        parse_statement(HEADER)


def test_bad_row_reports_line_number() -> None:
    bad = HEADER + row("15.12.25", "RENT PAYMENT", "Rent", "Landlord", "abc")
    with pytest.raises(StatementImportError, match="line 2"):
        parse_statement(bad)


def test_foreign_currency_without_rate_is_rejected() -> None:
    bad = HEADER + row(
        "15.12.25", "INTERNATIONAL WIRE TRANSFER", "No rate", "Luigi", "-1,00", "CHF"
    )
    with pytest.raises(StatementImportError, match="rate"):
        parse_statement(bad)


# --- The organizers' sample file (kept in gitignored local/) ---------------------------------

SAMPLE = get_settings().statement_path
needs_sample = pytest.mark.skipif(not SAMPLE.exists(), reason=f"sample CSV not found at {SAMPLE}")


@pytest.fixture(scope="module")
def sample():
    return load_statement(SAMPLE)


@needs_sample
def test_sample_imports_all_rows(sample) -> None:
    assert len(sample.transactions) == 1125
    assert sample.possible_duplicates == 0
    assert sample.account == ACCOUNT
    assert (sample.date_from, sample.date_to) == (date(2025, 1, 1), date(2025, 12, 31))
    assert Category.OTHER not in {t.category for t in sample.transactions}


@needs_sample
def test_sample_chf_tranches(sample) -> None:
    tranches = [t for t in sample.transactions if t.original_currency == "CHF"]
    assert len(tranches) == 12
    december = next(t for t in tranches if t.booking_date == date(2025, 12, 15))
    assert december.amount == -436250
    assert min(-t.amount for t in tranches) == 432500
    assert max(-t.amount for t in tranches) == 437500


@needs_sample
def test_sample_balances_match_published_figures(sample) -> None:
    opening = get_settings().opening_balance
    balances = daily_closing_balances(
        opening, sample.transactions, date(2025, 1, 1), date(2025, 12, 31)
    )

    assert balances[date(2025, 6, 30)] == -2009869
    assert balances[date(2025, 7, 3)] == -2209760
    assert min(balances.values()) == -2209760
    assert balances[date(2025, 12, 31)] == 3326635
    assert balance_before(opening, sample.transactions, date(2025, 5, 5)) == 40196
