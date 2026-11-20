from datetime import date, timedelta
from uuid import uuid4

import pytest

from api.config import get_settings
from api.domain.credit import round_up, size_credit_line
from api.domain.forecast import ForecastInput
from api.domain.models import (
    Category,
    CreditLine,
    Outflow,
    OutflowSource,
    PlannedEvent,
    PlannedEventKind,
)
from api.domain.revenue import RevenueModel, WeekdayAverage
from api.state import build_demo_state

START = date(2025, 1, 6)
UNTIL = date(2025, 3, 31)


def scenario(outflows: list[tuple[int, int]], credit_lines=(), floor=0) -> ForecastInput:
    return ForecastInput(
        as_of=START,
        start_balance=1000,
        cash_floor=floor,
        revenue_model=RevenueModel(
            "WEEKDAY_AVERAGE",
            START - timedelta(weeks=8),
            START - timedelta(days=1),
            8,
            tuple(WeekdayAverage(d, 100, 0) for d in range(7)),
        ),
        outflows=tuple(
            Outflow(
                id=uuid4(),
                ref=f"O{i}",
                counterparty="Supplier",
                category=Category.RENT,
                amount=amount,
                due_date=START + timedelta(days=day),
                scheduled_date=START + timedelta(days=day),
                latest_allowed_date=None,
                source=OutflowSource.DETECTED,
            )
            for i, (day, amount) in enumerate(outflows)
        ),
        credit_lines=tuple(credit_lines),
        horizon_days=20,
    )


@pytest.mark.parametrize(
    ("amount", "step", "expected"),
    [(1, 500, 500), (500, 500, 500), (501, 500, 1000), (1_608_111, 500_000, 2_000_000)],
)
def test_round_up(amount: int, step: int, expected: int) -> None:
    assert round_up(amount, step) == expected


def test_no_gap_needs_no_credit() -> None:
    sizing = size_credit_line(scenario([(3, 500)]), UNTIL, 500)

    assert not sizing.needed
    assert sizing.credit_line is None
    assert sizing.shortfall == 0


def test_line_covers_the_worst_day_rounded_up() -> None:
    sizing = size_credit_line(scenario([(2, 3000), (5, 1000)]), UNTIL, 500)

    assert sizing.needed
    assert sizing.shortfall == 2400
    assert sizing.credit_line == CreditLine(2500, START + timedelta(days=2), UNTIL)
    assert sizing.forecast_with_line.first_breach is None
    assert sizing.headroom_at_lowest == 100


def test_line_is_extended_when_the_gap_outlasts_the_term() -> None:
    sizing = size_credit_line(scenario([(2, 30_000)]), START + timedelta(days=5), 500)
    assert sizing.credit_line.available_until == START + timedelta(days=19)
    assert sizing.forecast_with_line.days_below_floor == 0


def test_existing_credit_counts_towards_the_gap() -> None:
    existing = CreditLine(1000, START, UNTIL)
    sizing = size_credit_line(scenario([(2, 3000)], credit_lines=[existing]), UNTIL, 500)

    assert sizing.shortfall == 700
    assert sizing.credit_line.amount == 1000
    assert sizing.forecast_with_line.first_breach is None


def test_rounding_must_be_positive() -> None:
    with pytest.raises(ValueError):
        size_credit_line(scenario([]), UNTIL, 0)


SETTINGS = get_settings()
needs_sample = pytest.mark.skipif(not SETTINGS.statement_path.exists(), reason="no sample CSV")


def closed_in_june():
    state = build_demo_state(SETTINGS)
    state.planned_events.append(
        PlannedEvent(uuid4(), PlannedEventKind.CLOSURE, date(2025, 6, 1), date(2025, 6, 30))
    )
    return state


@needs_sample
def test_s2_credit_line_after_planner() -> None:
    sizing = closed_in_june().credit_sizing(
        SETTINGS.credit_line_available_until, SETTINGS.credit_line_rounding
    )

    assert sizing.shortfall == 1_608_111
    assert sizing.credit_line == CreditLine(2_000_000, date(2025, 6, 27), date(2025, 9, 30))
    assert sizing.headroom_at_lowest == 391_889
    assert sizing.forecast_with_line.first_breach is None
    assert sizing.forecast_with_line.lowest.closing == -2_108_111


@needs_sample
def test_s1_credit_line_without_moves() -> None:
    sizing = closed_in_june().credit_sizing(
        SETTINGS.credit_line_available_until, SETTINGS.credit_line_rounding, apply_plan=False
    )

    assert sizing.shortfall == 1_776_082
    assert sizing.credit_line == CreditLine(2_000_000, date(2025, 6, 15), date(2025, 9, 30))
    assert sizing.forecast_with_line.first_breach is None


@needs_sample
def test_no_closure_no_credit() -> None:
    sizing = build_demo_state(SETTINGS).credit_sizing(date(2025, 9, 30), 500_000)
    assert not sizing.needed
