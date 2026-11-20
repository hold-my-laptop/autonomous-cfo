from datetime import date

import pytest

from api.config import get_settings
from api.dates import window_end
from api.domain.importer import load_statement
from api.domain.models import Category, OutflowSource
from api.domain.recurring import detect_recurring, project_outflows
from api.domain.schedule import ScheduledPayment, project_scheduled, sort_outflows
from api.seed import PIZZERIA_TAX_SCHEDULE

AS_OF = date(2025, 5, 5)
END = date(2025, 8, 3)


def payment(**overrides) -> ScheduledPayment:
    values = dict(
        ref_prefix="QTAX",
        counterparty="Tax Office",
        description="Quarterly tax",
        category=Category.TAX,
        amount=10_000,
        day_of_month=10,
        months=frozenset({3, 6, 9, 12}),
    )
    return ScheduledPayment(**(values | overrides))


def test_projects_only_listed_months_within_window() -> None:
    outflows = project_scheduled([payment()], date(2025, 1, 1), date(2025, 12, 31))

    assert [o.ref for o in outflows] == ["QTAX-03", "QTAX-06", "QTAX-09", "QTAX-12"]
    assert all(o.source is OutflowSource.OWNER for o in outflows)
    assert all(o.latest_allowed_date is None for o in outflows)
    assert outflows[0].description == "Quarterly tax"


def test_window_bounds_are_inclusive() -> None:
    assert [o.due_date for o in project_scheduled([payment()], date(2025, 6, 10), END)] == [
        date(2025, 6, 10)
    ]
    assert project_scheduled([payment()], date(2025, 6, 11), date(2025, 9, 9)) == []


def test_day_is_clamped_in_short_months() -> None:
    [feb] = project_scheduled(
        [payment(day_of_month=31, months=frozenset({2}))], date(2025, 1, 1), date(2025, 3, 31)
    )
    assert feb.due_date == date(2025, 2, 28)


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": 0},
        {"amount": -5},
        {"day_of_month": 32},
        {"months": frozenset()},
        {"months": {13}},
    ],
)
def test_invalid_schedule_is_rejected(overrides) -> None:
    with pytest.raises(ValueError):
        payment(**overrides)


def test_pizzeria_tax_schedule_in_demo_window() -> None:
    outflows = project_scheduled(PIZZERIA_TAX_SCHEDULE, AS_OF, END)

    assert [(o.ref, o.due_date, o.amount, o.description) for o in outflows] == [
        ("TTAX-05", date(2025, 5, 15), 22_500, "Trade tax prepayment"),
        ("ITAX-06", date(2025, 6, 10), 115_000, "Income tax prepayment"),
    ]


SAMPLE = get_settings().statement_path
needs_sample = pytest.mark.skipif(not SAMPLE.exists(), reason=f"sample CSV not found at {SAMPLE}")


@needs_sample
def test_all_known_outflows_match_design_table() -> None:
    settings = get_settings()
    end = window_end(AS_OF, settings.forecast_horizon_days)
    detected = project_outflows(
        detect_recurring(load_statement(SAMPLE).transactions, AS_OF),
        AS_OF,
        end,
        {Category.PARTNER_TRANSFER: settings.partner_transfer_movable_days},
    )
    outflows = sort_outflows(detected + project_scheduled(PIZZERIA_TAX_SCHEDULE, AS_OF, end))

    assert len(outflows) == 20
    assert [o.ref for o in outflows] == [
        "UTIL-05",
        "WTAX-05",
        "CHF-05",
        "TTAX-05",
        "SOC-05",
        "PAY-05",
        "RENT-06",
        "ITAX-06",
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
    assert sum(o.amount for o in outflows) == 6_411_502
