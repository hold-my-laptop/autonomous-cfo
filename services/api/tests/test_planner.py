from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest

from api.config import get_settings
from api.domain.forecast import ForecastInput, compute_forecast
from api.domain.models import Category, Outflow, OutflowSource, PlannedEvent, PlannedEventKind
from api.domain.planner import PLANNER_VERSION, plan_moves
from api.domain.revenue import RevenueModel, WeekdayAverage
from api.state import build_demo_state

START = date(2025, 1, 6)
PROTECTED = frozenset({Category.PAYROLL, Category.SOCIAL_SECURITY, Category.TAX})


def model(revenue: int = 100) -> RevenueModel:
    return RevenueModel(
        "WEEKDAY_AVERAGE",
        START - timedelta(weeks=8),
        START - timedelta(days=1),
        8,
        tuple(WeekdayAverage(d, revenue, 0) for d in range(7)),
    )


def outflow(day: int, amount: int, ref: str, category=Category.PARTNER_TRANSFER, movable=20):
    due = START + timedelta(days=day)
    return Outflow(
        id=uuid4(),
        ref=ref,
        counterparty=ref,
        category=category,
        amount=amount,
        due_date=due,
        scheduled_date=due,
        latest_allowed_date=due + timedelta(days=movable) if movable else None,
        source=OutflowSource.DETECTED,
    )


def scenario(outflows, start_balance=1000, horizon=30) -> ForecastInput:
    return ForecastInput(
        as_of=START,
        start_balance=start_balance,
        cash_floor=0,
        revenue_model=model(),
        outflows=tuple(outflows),
        horizon_days=horizon,
    )


def test_no_breach_means_empty_plan() -> None:
    result = plan_moves(scenario([outflow(5, 500, "A")]), PROTECTED, 45)

    assert result.candidates == ()
    assert result.resolved
    assert result.residual_breach is None
    assert result.planner_version == PLANNER_VERSION


def test_moves_a_payment_to_its_latest_date() -> None:
    result = plan_moves(scenario([outflow(2, 2000, "A")]), PROTECTED, 45)

    [move] = result.candidates
    assert (move.ref, move.from_date, move.to_date) == (
        "A",
        START + timedelta(days=2),
        START + timedelta(days=22),
    )
    assert move.benefit.days_below_floor_saved > 0
    assert result.resolved


def test_max_delay_days_caps_the_target() -> None:
    [move] = plan_moves(scenario([outflow(2, 2000, "A")]), PROTECTED, 10).candidates
    assert move.to_date == START + timedelta(days=12)


def test_protected_unmovable_and_already_moved_are_skipped() -> None:
    payroll = outflow(2, 2000, "PAY", category=Category.PAYROLL)
    rent = outflow(2, 2000, "RENT", category=Category.RENT, movable=0)
    moved = outflow(2, 2000, "MOVED")
    moved = replace(moved, scheduled_date=moved.due_date + timedelta(days=1))
    result = plan_moves(scenario([payroll, rent, moved]), PROTECTED, 45)

    assert result.candidates == ()
    assert not result.resolved
    assert result.residual_breach is not None


def test_only_payments_due_before_the_breach_qualify() -> None:
    early = outflow(1, 2000, "EARLY")
    late = outflow(10, 5000, "LATE")
    refs = [c.ref for c in plan_moves(scenario([early, late]), PROTECTED, 45).candidates]
    assert refs[0] == "EARLY"


def test_larger_amount_first_then_furthest_target() -> None:
    small = outflow(1, 1500, "SMALL")
    big = outflow(1, 1600, "BIG")
    assert plan_moves(scenario([small, big]), PROTECTED, 45).candidates[0].ref == "BIG"

    near = outflow(1, 1500, "NEAR", movable=10)
    far = outflow(1, 1500, "FAR", movable=20)
    assert plan_moves(scenario([near, far]), PROTECTED, 45).candidates[0].ref == "FAR"


def test_moves_with_too_little_benefit_are_not_proposed() -> None:
    tiny = outflow(2, 2000, "TINY", movable=2)
    result = plan_moves(scenario([tiny]), PROTECTED, 45)
    assert result.candidates == ()


def test_input_is_not_modified() -> None:
    data = scenario([outflow(2, 2000, "A")])
    plan_moves(data, PROTECTED, 45)
    assert data.outflows[0].scheduled_date == data.outflows[0].due_date


SETTINGS = get_settings()
needs_sample = pytest.mark.skipif(not SETTINGS.statement_path.exists(), reason="no sample CSV")


@needs_sample
def test_demo_plan_postpones_only_the_june_transfer() -> None:
    state = build_demo_state(SETTINGS)
    state.planned_events.append(
        PlannedEvent(uuid4(), PlannedEventKind.CLOSURE, date(2025, 6, 1), date(2025, 6, 30))
    )
    result = state.plan()

    [move] = result.candidates
    assert (move.ref, move.from_date, move.to_date, move.amount) == (
        "CHF-06",
        date(2025, 6, 15),
        date(2025, 7, 30),
        433334,
    )
    assert (move.benefit.first_breach_delay_days, move.benefit.days_below_floor_saved) == (12, 12)
    assert not result.resolved
    s2 = result.forecast_if_all_applied
    assert (s2.first_breach.date, s2.first_breach.closing) == (date(2025, 6, 27), -583291)
    assert s2.days_below_floor == 38
    assert (s2.lowest.date, s2.lowest.closing) == (date(2025, 8, 2), -2108111)
    assert result.residual_breach == s2.first_breach


@needs_sample
def test_demo_without_closure_needs_no_plan() -> None:
    result = build_demo_state(SETTINGS).plan()
    assert result.candidates == ()
    assert result.resolved


@needs_sample
def test_applied_plan_matches_forecast_engine() -> None:
    state = build_demo_state(SETTINGS)
    state.planned_events.append(
        PlannedEvent(uuid4(), PlannedEventKind.CLOSURE, date(2025, 6, 1), date(2025, 6, 30))
    )
    [move] = state.plan().candidates
    data = state.forecast_input()
    applied = replace(
        data,
        outflows=tuple(
            replace(o, scheduled_date=move.to_date) if o.id == move.outflow_id else o
            for o in data.outflows
        ),
    )
    assert compute_forecast(applied).summary == state.plan().forecast_if_all_applied
