"""Expected daily card revenue and variable costs, learned from recent history."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from api.domain.models import Category, Transaction
from api.money import Cents

MODEL_VERSION = "revenue-1.0.0"
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@dataclass(frozen=True, slots=True)
class WeekdayAverage:
    """`revenue` is positive, `variable_costs` is negative."""

    weekday: int
    revenue: Cents
    variable_costs: Cents

    @property
    def name(self) -> str:
        return WEEKDAYS[self.weekday]


@dataclass(frozen=True, slots=True)
class RevenueModel:
    method: str
    window_from: date
    window_to: date
    weeks: int
    by_weekday: tuple[WeekdayAverage, ...]
    model_version: str = MODEL_VERSION

    def expected(self, day: date) -> WeekdayAverage:
        return self.by_weekday[day.weekday()]


def build_revenue_model(
    transactions: Iterable[Transaction], as_of: date, weeks: int = 8
) -> RevenueModel:
    """Weekday averages over the `weeks` full weeks before `as_of`.

    Sums are in cents and divided by `weeks` with floor division, so results are exact
    and reproducible."""
    if weeks < 1:
        raise ValueError("weeks must be at least 1")
    window_from = as_of - timedelta(weeks=weeks)
    window_to = as_of - timedelta(days=1)

    revenue = [0] * 7
    costs = [0] * 7
    for transaction in transactions:
        if not window_from <= transaction.booking_date <= window_to:
            continue
        weekday = transaction.booking_date.weekday()
        if transaction.category is Category.CARD_REVENUE:
            revenue[weekday] += transaction.amount
        elif transaction.category is Category.VARIABLE_COST:
            costs[weekday] += transaction.amount

    return RevenueModel(
        method="WEEKDAY_AVERAGE",
        window_from=window_from,
        window_to=window_to,
        weeks=weeks,
        by_weekday=tuple(
            WeekdayAverage(weekday=d, revenue=revenue[d] // weeks, variable_costs=costs[d] // weeks)
            for d in range(7)
        ),
    )
