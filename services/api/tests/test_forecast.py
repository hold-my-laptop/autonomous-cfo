from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from api.domain.forecast import ENGINE_VERSION, DriverKind, ForecastInput, compute_forecast
from api.domain.models import (
    Category,
    CreditLine,
    Inflow,
    Outflow,
    OutflowSource,
    PlannedEvent,
    PlannedEventKind,
)
from api.domain.revenue import RevenueModel, WeekdayAverage

MONDAY = date(2025, 1, 6)


def flat_model(revenue: int = 100, costs: int = -40) -> RevenueModel:
    return RevenueModel(
        method="WEEKDAY_AVERAGE",
        window_from=date(2024, 11, 11),
        window_to=date(2025, 1, 5),
        weeks=8,
        by_weekday=tuple(WeekdayAverage(d, revenue, costs) for d in range(7)),
    )


def outflow(day: date, amount: int, ref: str = "OUT-01") -> Outflow:
    return Outflow(
        id=uuid4(),
        ref=ref,
        counterparty="Supplier",
        category=Category.RENT,
        amount=amount,
        due_date=day,
        scheduled_date=day,
        latest_allowed_date=None,
        source=OutflowSource.DETECTED,
    )


def closure(start: date, end: date, costs_stop: bool = True) -> PlannedEvent:
    return PlannedEvent(uuid4(), PlannedEventKind.CLOSURE, start, end, costs_stop)


def small(**overrides) -> ForecastInput:
    values = dict(
        as_of=MONDAY,
        start_balance=1000,
        cash_floor=900,
        revenue_model=flat_model(),
        outflows=[outflow(date(2025, 1, 7), 500)],
        horizon_days=5,
    )
    return ForecastInput(**(values | overrides))


def test_hand_computed_balances() -> None:
    forecast = compute_forecast(small())

    assert [d.closing for d in forecast.days] == [1060, 620, 680, 740, 800]
    assert [d.below_floor for d in forecast.days] == [False, True, True, True, True]
    day = forecast.days[1]
    assert (day.revenue, day.variable_costs, day.fixed_outflows, day.other_inflows) == (
        100,
        -40,
        -500,
        0,
    )
    summary = forecast.summary
    assert (summary.first_breach.date, summary.first_breach.closing) == (date(2025, 1, 7), 620)
    assert summary.first_breach.shortfall == 280
    assert (summary.lowest.date, summary.lowest.closing) == (date(2025, 1, 7), 620)
    assert (summary.days_below_floor, summary.end_balance, summary.headroom_at_lowest) == (
        4,
        800,
        -280,
    )
    assert forecast.engine_version == ENGINE_VERSION


def test_negative_balances_are_not_clamped() -> None:
    forecast = compute_forecast(small(start_balance=-5000, cash_floor=-10_000))
    assert forecast.days[0].closing == -4940
    assert forecast.summary.first_breach is None


def test_closure_stops_revenue_and_optionally_variable_costs() -> None:
    stops = compute_forecast(small(planned_events=[closure(date(2025, 1, 8), date(2025, 1, 9))]))
    assert [d.closed for d in stops.days] == [False, False, True, True, False]
    assert [(d.revenue, d.variable_costs) for d in stops.days[2:4]] == [(0, 0), (0, 0)]

    keeps = compute_forecast(
        small(planned_events=[closure(date(2025, 1, 8), date(2025, 1, 9), costs_stop=False)])
    )
    assert [(d.revenue, d.variable_costs) for d in keeps.days[2:4]] == [(0, -40), (0, -40)]


def test_credit_line_adds_headroom_from_its_start() -> None:
    line = CreditLine(
        amount=300, available_from=date(2025, 1, 7), available_until=date(2025, 3, 31)
    )
    forecast = compute_forecast(small(credit_lines=[line]))

    assert [d.credit_available for d in forecast.days] == [0, 300, 300, 300, 300]
    assert forecast.summary.first_breach is None
    assert forecast.summary.headroom_at_lowest == 20
    assert forecast.days[1].closing == 620


def test_known_inflows_and_missing_inflow_driver() -> None:
    late = Inflow(uuid4(), "IN-01", "Customer", 1000, date(2025, 1, 10), date(2025, 1, 7))
    forecast = compute_forecast(small(inflows=[late]))

    assert forecast.days[-1].other_inflows == 1000
    assert forecast.days[-1].closing == 1800
    missing = [d for d in forecast.drivers if d.kind is DriverKind.MISSING_INFLOW]
    assert [(d.ref, d.amount) for d in missing] == [("IN-01", 1000)]


def test_window_bounds_are_inclusive() -> None:
    forecast = compute_forecast(small(outflows=[outflow(date(2025, 1, 10), 100)]))
    assert forecast.days[-1].fixed_outflows == -100
    beyond = compute_forecast(small(outflows=[outflow(date(2025, 1, 11), 100)]))
    assert sum(d.fixed_outflows for d in beyond.days) == 0


def test_no_breach_means_no_breach_drivers() -> None:
    forecast = compute_forecast(small(cash_floor=0))
    assert forecast.summary.first_breach is None
    assert forecast.drivers == ()


def test_horizon_must_be_positive() -> None:
    with pytest.raises(ValueError):
        compute_forecast(small(horizon_days=0))


def june_closure() -> PlannedEvent:
    return closure(date(2025, 6, 1), date(2025, 6, 30))


def test_s0_baseline(demo_input: ForecastInput) -> None:
    forecast = compute_forecast(demo_input)
    summary = forecast.summary

    assert forecast.start_balance == 40196
    assert len(forecast.days) == 91
    assert forecast.days[-1].date == date(2025, 8, 3)
    assert summary.first_breach is None
    assert (summary.lowest.date, summary.lowest.closing) == (date(2025, 6, 1), -39630)
    assert summary.days_below_floor == 0
    assert summary.end_balance == 239220


def test_s1_june_closure(demo_input: ForecastInput) -> None:
    forecast = compute_forecast(replace(demo_input, planned_events=[june_closure()]))
    summary = forecast.summary

    assert (summary.first_breach.date, summary.first_breach.closing) == (date(2025, 6, 15), -751625)
    assert summary.first_breach.shortfall == 251625
    assert (summary.lowest.date, summary.lowest.closing) == (date(2025, 7, 3), -2276082)
    assert summary.days_below_floor == 50
    assert summary.end_balance == -2097450
    assert sum(d.closed for d in forecast.days) == 30


def test_s1_drivers_and_assumptions(demo_input: ForecastInput) -> None:
    forecast = compute_forecast(replace(demo_input, planned_events=[june_closure()]))

    no_revenue = [d for d in forecast.drivers if d.kind is DriverKind.NO_REVENUE]
    assert [(d.amount, d.date_from, d.date_to) for d in no_revenue] == [
        (4590578, date(2025, 6, 1), date(2025, 6, 30))
    ]
    outflows = [d.ref for d in forecast.drivers if d.kind is DriverKind.OUTFLOW]
    assert outflows == ["PAY-05", "CHF-05", "CHF-06", "RENT-06", "SOC-05"]
    assert [a.code for a in forecast.assumptions] == [
        "REVENUE_WEEKDAY_AVERAGE_8W",
        "CALENDAR_DAYS",
        "CLOSING_DAY_CONVENTION",
        "CHF_AT_LAST_RATE",
        "OWNER_SCHEDULE",
        "CLOSURE_STOPS_VARIABLE_COSTS",
    ]


def postpone(data: ForecastInput, ref: str, to: date) -> ForecastInput:
    return replace(
        data,
        outflows=[replace(o, scheduled_date=to) if o.ref == ref else o for o in data.outflows],
    )


def test_s2_june_transfer_postponed(demo_input: ForecastInput) -> None:
    s2 = postpone(replace(demo_input, planned_events=[june_closure()]), "CHF-06", date(2025, 7, 30))
    summary = compute_forecast(s2).summary

    assert (summary.first_breach.date, summary.first_breach.closing) == (date(2025, 6, 27), -583291)
    assert summary.days_below_floor == 38
    assert (summary.lowest.date, summary.lowest.closing) == (date(2025, 8, 2), -2108111)
    assert summary.end_balance == -2097450


def test_s3_with_credit_line(demo_input: ForecastInput) -> None:
    s2 = postpone(replace(demo_input, planned_events=[june_closure()]), "CHF-06", date(2025, 7, 30))
    line = CreditLine(2_000_000, date(2025, 6, 27), date(2025, 9, 30))
    summary = compute_forecast(replace(s2, credit_lines=[line])).summary

    assert summary.first_breach is None
    assert summary.days_below_floor == 0
    assert (summary.lowest.date, summary.lowest.closing) == (date(2025, 8, 2), -2108111)
    assert summary.headroom_at_lowest == 391889
