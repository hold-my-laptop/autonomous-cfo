from datetime import date
from uuid import uuid4

import pytest

from api.config import get_settings
from api.dates import window_end
from api.domain.importer import load_statement
from api.domain.models import Category, OutflowSource, Transaction
from api.domain.recurring import detect_recurring, project_outflows

AS_OF = date(2025, 5, 5)


def tx(day: date, amount: int, category=Category.RENT, counterparty="Landlord", **extra):
    return Transaction(
        id=uuid4(),
        account="DE00",
        booking_date=day,
        value_date=day,
        type=extra.pop("type", "RENT PAYMENT"),
        counterparty=counterparty,
        counterparty_account="",
        reference="",
        amount=amount,
        category=category,
        **extra,
    )


def monthly(day_of_month: int, amounts: list[int], months=(1, 2, 3, 4), **kwargs):
    return [
        tx(date(2025, m, day_of_month), a, **kwargs) for m, a in zip(months, amounts, strict=True)
    ]


def test_detects_stable_monthly_payment() -> None:
    [rent] = detect_recurring(monthly(1, [-380000] * 4), AS_OF)

    assert (rent.ref_prefix, rent.day_of_month, rent.amount) == ("RENT", 1, 380000)
    assert rent.occurrences == 4
    assert rent.last_seen == date(2025, 4, 1)


def test_needs_three_consecutive_months() -> None:
    assert detect_recurring(monthly(1, [-1000] * 2, months=(3, 4)), AS_OF) == []
    assert detect_recurring(monthly(1, [-1000] * 3, months=(1, 2, 4)), AS_OF) == []


def test_amount_drift_over_two_percent_is_not_recurring() -> None:
    assert detect_recurring(monthly(1, [-1000, -1000, -1000, -1030]), AS_OF) == []
    assert len(detect_recurring(monthly(1, [-1000, -1000, -1000, -1015]), AS_OF)) == 1


def test_day_drift_over_one_day_is_not_recurring() -> None:
    items = [tx(date(2025, m, d), -1000) for m, d in ((1, 10), (2, 10), (3, 13), (4, 10))]
    assert detect_recurring(items, AS_OF) == []


def test_stale_payment_is_not_projected() -> None:
    assert detect_recurring(monthly(1, [-1000] * 3, months=(1, 2, 3)), AS_OF) == []


def test_transactions_on_or_after_as_of_are_ignored() -> None:
    assert detect_recurring(monthly(5, [-1000] * 3, months=(5, 6, 7)), AS_OF) == []


def test_variable_costs_and_income_are_ignored() -> None:
    items = monthly(1, [-1000] * 4, category=Category.VARIABLE_COST)
    items += monthly(2, [5000] * 4, category=Category.CARD_REVENUE)
    assert detect_recurring(items, AS_OF) == []


def test_payroll_is_summed_per_day() -> None:
    items = []
    for name, amount in (("Rosa", -210000), ("Yusuf", -235000)):
        items += monthly(28, [amount] * 4, category=Category.PAYROLL, counterparty=name)
    [payroll] = detect_recurring(items, AS_OF)

    assert payroll.amount == 445000
    assert payroll.counterparty == "Payroll (2 people)"


def test_foreign_currency_uses_original_amount_and_last_rate() -> None:
    rates = ("1.042", "1.045", "1.038", "1.040")
    items = [
        tx(
            date(2025, m, 15),
            -round(416667 * float(r)),
            category=Category.PARTNER_TRANSFER,
            counterparty="Luigi",
            original_amount=-416667,
            original_currency="CHF",
            fx_rate=r,
        )
        for m, r in zip((1, 2, 3, 4), rates, strict=True)
    ]
    [transfer] = detect_recurring(items, AS_OF)

    assert transfer.ref_prefix == "CHF"
    assert (transfer.original_amount, transfer.fx_rate, transfer.amount) == (
        416667,
        "1.040",
        433334,
    )


def test_projection_respects_window_and_short_months() -> None:
    [rent] = detect_recurring(monthly(30, [-1000] * 3, months=(3, 4, 5)), date(2025, 6, 5))
    outflows = project_outflows([rent], date(2026, 2, 1), date(2026, 3, 31))
    assert [o.due_date for o in outflows] == [date(2026, 2, 28), date(2026, 3, 30)]
    assert project_outflows([rent], date(2026, 3, 31), date(2026, 4, 29)) == []


def test_movable_categories_get_latest_allowed_date() -> None:
    [rent] = detect_recurring(monthly(1, [-1000] * 4), AS_OF)
    [outflow] = project_outflows([rent], date(2025, 6, 1), date(2025, 6, 30), {Category.RENT: 10})
    assert outflow.latest_allowed_date == date(2025, 6, 11)

    [fixed] = project_outflows([rent], date(2025, 6, 1), date(2025, 6, 30))
    assert fixed.latest_allowed_date is None
    assert fixed.source is OutflowSource.DETECTED


SAMPLE = get_settings().statement_path
needs_sample = pytest.mark.skipif(not SAMPLE.exists(), reason=f"sample CSV not found at {SAMPLE}")


@pytest.fixture(scope="module")
def recurring():
    return detect_recurring(load_statement(SAMPLE).transactions, AS_OF)


@needs_sample
def test_sample_detects_the_monthly_fixed_costs(recurring) -> None:
    found = {(r.ref_prefix, r.category, r.day_of_month, r.amount) for r in recurring}

    assert found == {
        ("RENT", Category.RENT, 1, 380000),
        ("WTAX", Category.TAX, 10, 88000),
        ("UTIL", Category.UTILITIES, 10, 65000),
        ("CHF", Category.PARTNER_TRANSFER, 15, 433334),
        ("SOC", Category.SOCIAL_SECURITY, 27, 265000),
        ("PAY", Category.PAYROLL, 28, 860000),
    }
    payroll = next(r for r in recurring if r.category is Category.PAYROLL)
    assert payroll.counterparty == "Payroll (4 people)"
    chf = next(r for r in recurring if r.ref_prefix == "CHF")
    assert (chf.original_amount, chf.original_currency, chf.fx_rate) == (416667, "CHF", "1.040")


@needs_sample
def test_sample_quarterly_taxes_are_not_detected(recurring) -> None:
    assert all(r.amount not in (115000, 22500) for r in recurring)


@needs_sample
def test_sample_projection_matches_design_outflow_table(recurring) -> None:
    settings = get_settings()
    end = window_end(AS_OF, settings.forecast_horizon_days)
    outflows = project_outflows(
        recurring,
        AS_OF,
        end,
        {Category.PARTNER_TRANSFER: settings.partner_transfer_movable_days},
    )

    assert end == date(2025, 8, 3)
    assert [o.ref for o in outflows] == [
        "UTIL-05",
        "WTAX-05",
        "CHF-05",
        "SOC-05",
        "PAY-05",
        "RENT-06",
        "UTIL-06",
        "WTAX-06",
        "CHF-06",
        "SOC-06",
        "PAY-06",
        "RENT-07",
        "UTIL-07",
        "WTAX-07",
        "CHF-07",
        "SOC-07",
        "PAY-07",
        "RENT-08",
    ]
    movable = {o.ref: o.latest_allowed_date for o in outflows if o.latest_allowed_date}
    assert movable == {
        "CHF-05": date(2025, 6, 29),
        "CHF-06": date(2025, 7, 30),
        "CHF-07": date(2025, 8, 29),
    }
    assert sum(o.amount for o in outflows) == (
        3 * (65000 + 88000 + 433334 + 265000 + 860000) + 3 * 380000
    )
