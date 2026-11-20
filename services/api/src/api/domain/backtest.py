"""Compare a forecast with the balances that really happened (possible on the sample year)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from api.domain.balances import balance_before, daily_closing_balances
from api.domain.forecast import Breach, DayBalance, Forecast
from api.domain.models import Transaction
from api.money import Cents


@dataclass(frozen=True, slots=True)
class BacktestDay:
    date: date
    forecast: Cents
    actual: Cents

    @property
    def difference(self) -> Cents:
        return self.forecast - self.actual


@dataclass(frozen=True, slots=True)
class BalanceSummary:
    first_breach: Breach | None
    lowest: DayBalance
    days_below_floor: int
    end_balance: Cents


@dataclass(frozen=True, slots=True)
class Backtest:
    """`complete` is False when the statement ends before the forecast window does."""

    days: tuple[BacktestDay, ...]
    forecast: BalanceSummary
    actual: BalanceSummary
    complete: bool
    mean_abs_error: Cents
    lowest_error: Cents
    lowest_error_bp: int
    note: str


def _summarize(points: Sequence[tuple[date, Cents]], cash_floor: Cents) -> BalanceSummary:
    breaches = [(day, closing) for day, closing in points if closing < cash_floor]
    lowest = min(points, key=lambda point: point[1])
    first = breaches[0] if breaches else None
    return BalanceSummary(
        first_breach=Breach(date=first[0], closing=first[1], shortfall=cash_floor - first[1])
        if first
        else None,
        lowest=DayBalance(date=lowest[0], closing=lowest[1]),
        days_below_floor=len(breaches),
        end_balance=points[-1][1],
    )


def compute_backtest(
    forecast: Forecast, opening_balance: Cents, transactions: Sequence[Transaction]
) -> Backtest:
    """Actual balances use the same opening balance and floor as the forecast, without credit."""
    last_booked = max((t.booking_date for t in transactions), default=None)
    covered = [d for d in forecast.days if last_booked is not None and d.date <= last_booked]
    if not covered:
        raise ValueError("the statement has no transactions in the forecast window")

    start = balance_before(opening_balance, transactions, forecast.as_of)
    actual = daily_closing_balances(start, transactions, forecast.as_of, covered[-1].date)
    days = tuple(BacktestDay(d.date, d.closing, actual[d.date]) for d in covered)

    forecast_summary = _summarize([(d.date, d.forecast) for d in days], forecast.cash_floor)
    actual_summary = _summarize([(d.date, d.actual) for d in days], forecast.cash_floor)
    lowest_error = forecast_summary.lowest.closing - actual_summary.lowest.closing
    lowest_actual = abs(actual_summary.lowest.closing)
    return Backtest(
        days=days,
        forecast=forecast_summary,
        actual=actual_summary,
        complete=len(covered) == len(forecast.days),
        mean_abs_error=sum(abs(d.difference) for d in days) // len(days),
        lowest_error=lowest_error,
        lowest_error_bp=abs(lowest_error) * 10_000 // lowest_actual if lowest_actual else 0,
        note=f"Backtest on the sample year: forecast made on {forecast.as_of:%d %b %Y} "
        f"compared with the balance that was actually booked.",
    )
