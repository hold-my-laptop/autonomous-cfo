"""Monte Carlo cash forecast: a range (P10/P50/P90) around the deterministic forecast.

Method (bootstrap by weekday, no extra libraries):
- From the same window the revenue model uses (the `weeks` full weeks before `as_of`),
  collect every real day's card revenue and variable costs, grouped by weekday.
  Credit-card payouts land only on some weekdays, so sampling within the weekday keeps
  that pattern.
- Each run walks the horizon day by day. Known items (fixed outflows, other inflows,
  closures) are taken from the deterministic forecast; only revenue and variable costs
  are replaced by a randomly drawn real day of the same weekday.
- Percentiles are nearest-rank over the runs. Money stays in integer cents.

P10 is the pessimistic case (10 % of runs end lower), P90 the optimistic one.
Same inputs and seed give the same result.
"""

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from api.domain.forecast import Forecast, ForecastInput, compute_forecast
from api.domain.models import Category, Transaction
from api.money import Cents

ENGINE_VERSION = "montecarlo-1.0.0"
DEFAULT_RUNS = 1000
DEFAULT_SEED = 42


@dataclass(frozen=True, slots=True)
class DaySample:
    """One real historical day: revenue is positive, variable_costs negative."""

    day: date
    revenue: Cents
    variable_costs: Cents


@dataclass(frozen=True, slots=True)
class DayBand:
    date: date
    p10: Cents
    p50: Cents
    p90: Cents


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    runs: int
    seed: int
    window_from: date
    window_to: date
    lowest_p10: Cents
    lowest_p50: Cents
    lowest_p90: Cents
    breach_probability_bp: int  # share of runs that go below the cash floor, 10000 = 100 %
    days: tuple[DayBand, ...]
    deterministic: Forecast
    engine_version: str = ENGINE_VERSION

    @property
    def breach_probability_pct(self) -> float:
        return self.breach_probability_bp / 100


def collect_day_samples(
    transactions: Iterable[Transaction], as_of: date, weeks: int = 8
) -> dict[int, list[DaySample]]:
    """Real days of the revenue-model window, by weekday (0 = Monday)."""
    window_from = as_of - timedelta(weeks=weeks)
    window_to = as_of - timedelta(days=1)
    revenue: dict[date, Cents] = {}
    costs: dict[date, Cents] = {}
    for t in transactions:
        if not window_from <= t.booking_date <= window_to:
            continue
        if t.category is Category.CARD_REVENUE:
            revenue[t.booking_date] = revenue.get(t.booking_date, 0) + t.amount
        elif t.category is Category.VARIABLE_COST:
            costs[t.booking_date] = costs.get(t.booking_date, 0) + t.amount
    by_weekday: dict[int, list[DaySample]] = {d: [] for d in range(7)}
    day = window_from
    while day <= window_to:
        by_weekday[day.weekday()].append(DaySample(day, revenue.get(day, 0), costs.get(day, 0)))
        day += timedelta(days=1)
    return by_weekday


def percentile(sorted_values: Sequence[int], pct: int) -> int:
    """Nearest-rank percentile of an ascending list (pct 0-100)."""
    if not sorted_values:
        raise ValueError("no values")
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def simulate(
    data: ForecastInput,
    samples: dict[int, list[DaySample]],
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> MonteCarloResult:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if any(not samples.get(d) for d in range(7)):
        raise ValueError("every weekday needs at least one historical day")

    base = compute_forecast(data)
    closures = {e.id: e for e in data.planned_events}
    rng = random.Random(seed)

    # Per day: known flows and closure behaviour, taken from the deterministic forecast.
    plan = []
    for row in base.days:
        stops_costs = row.closed and any(
            e.variable_costs_stop and e.covers(row.date) for e in closures.values()
        )
        plan.append((row.date.weekday(), row.fixed_outflows + row.other_inflows, row.closed, stops_costs,
                     row.credit_available))

    lowest_per_run: list[int] = []
    closing_per_day: list[list[int]] = [[] for _ in plan]
    breaches = 0
    for _ in range(runs):
        closing = data.start_balance
        lowest = None
        breached = False
        for i, (weekday, known, closed, stops_costs, credit) in enumerate(plan):
            s = rng.choice(samples[weekday])
            revenue = 0 if closed else s.revenue
            costs = 0 if stops_costs else s.variable_costs
            closing += revenue + costs + known
            closing_per_day[i].append(closing)
            lowest = closing if lowest is None or closing < lowest else lowest
            if closing + credit < data.cash_floor:
                breached = True
        lowest_per_run.append(lowest)
        breaches += breached

    lowest_per_run.sort()
    bands = []
    for row, values in zip(base.days, closing_per_day):
        values.sort()
        bands.append(DayBand(row.date, percentile(values, 10), percentile(values, 50), percentile(values, 90)))

    window = [s.day for day_list in samples.values() for s in day_list]
    return MonteCarloResult(
        runs=runs,
        seed=seed,
        window_from=min(window),
        window_to=max(window),
        lowest_p10=percentile(lowest_per_run, 10),
        lowest_p50=percentile(lowest_per_run, 50),
        lowest_p90=percentile(lowest_per_run, 90),
        breach_probability_bp=breaches * 10_000 // runs,
        days=tuple(bands),
        deterministic=base,
    )


def credit_need(lowest: Cents, cash_floor: Cents, round_to: Cents = 500_000) -> Cents:
    """Credit line that keeps `lowest` at or above the floor, rounded up to `round_to`
    (contract: 5 000 €). 0 when no gap. Use it with `lowest_p10` for a cautious size."""
    gap = cash_floor - lowest
    if gap <= 0:
        return 0
    return -(-gap // round_to) * round_to
