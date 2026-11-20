from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from api.config import get_settings
from api.domain.backtest import compute_backtest
from api.domain.forecast import ForecastInput, compute_forecast
from api.domain.importer import StatementImport
from api.domain.models import Category, PlannedEvent, PlannedEventKind, Transaction
from api.domain.revenue import RevenueModel, WeekdayAverage

AS_OF = date(2025, 1, 6)


def tx(day: date, amount: int) -> Transaction:
    return Transaction(
        id=uuid4(),
        account="DE00",
        booking_date=day,
        value_date=day,
        type="TEST",
        counterparty="Someone",
        counterparty_account="",
        reference="",
        amount=amount,
        category=Category.CARD_REVENUE,
    )


def flat_forecast(horizon: int = 3, floor: int = 0):
    model = RevenueModel(
        method="WEEKDAY_AVERAGE",
        window_from=date(2024, 11, 11),
        window_to=date(2025, 1, 5),
        weeks=8,
        by_weekday=tuple(WeekdayAverage(d, 100, 0) for d in range(7)),
    )
    return compute_forecast(
        ForecastInput(
            as_of=AS_OF,
            start_balance=1000,
            cash_floor=floor,
            revenue_model=model,
            horizon_days=horizon,
        )
    )


def test_compares_forecast_with_actual_balances() -> None:
    transactions = [
        tx(date(2025, 1, 1), 1000),
        tx(date(2025, 1, 6), 50),
        tx(date(2025, 1, 7), -1200),
        tx(date(2025, 1, 8), 300),
    ]
    backtest = compute_backtest(flat_forecast(), 0, transactions)

    assert [(d.forecast, d.actual, d.difference) for d in backtest.days] == [
        (1100, 1050, 50),
        (1200, -150, 1350),
        (1300, 150, 1150),
    ]
    assert backtest.complete
    assert backtest.actual.first_breach.date == date(2025, 1, 7)
    assert backtest.actual.first_breach.shortfall == 150
    assert backtest.forecast.first_breach is None
    assert backtest.mean_abs_error == 850
    assert backtest.lowest_error == 1100 - -150
    assert backtest.lowest_error_bp == 1250 * 10_000 // 150


def test_incomplete_when_statement_ends_early() -> None:
    backtest = compute_backtest(flat_forecast(horizon=5), 0, [tx(date(2025, 1, 7), 1000)])

    assert not backtest.complete
    assert [d.date for d in backtest.days] == [date(2025, 1, 6), date(2025, 1, 7)]


def test_no_overlap_is_an_error() -> None:
    with pytest.raises(ValueError):
        compute_backtest(flat_forecast(), 0, [tx(date(2025, 1, 1), 1000)])


def test_sample_actual_2025_matches_design(
    demo_input: ForecastInput, sample_statement: StatementImport
) -> None:
    closure = PlannedEvent(uuid4(), PlannedEventKind.CLOSURE, date(2025, 6, 1), date(2025, 6, 30))
    forecast = compute_forecast(replace(demo_input, planned_events=[closure]))
    backtest = compute_backtest(
        forecast, get_settings().opening_balance, sample_statement.transactions
    )
    actual, predicted = backtest.actual, backtest.forecast

    assert backtest.complete
    assert len(backtest.days) == 91
    assert backtest.days[0].actual == 40196 + sum(
        t.amount for t in sample_statement.transactions if t.booking_date == date(2025, 5, 5)
    )
    assert (actual.first_breach.date, actual.first_breach.closing) == (date(2025, 6, 15), -884869)
    assert (actual.lowest.date, actual.lowest.closing) == (date(2025, 7, 3), -2209760)
    assert actual.days_below_floor == 48
    assert actual.end_balance == -1191455

    assert predicted.first_breach.date == actual.first_breach.date
    assert predicted.lowest.date == actual.lowest.date
    assert backtest.lowest_error == -66322
    assert backtest.lowest_error_bp == 300
