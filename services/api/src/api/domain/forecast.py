"""Deterministic daily cash forecast."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from api.dates import days, window_end
from api.domain.models import (
    CreditLine,
    Inflow,
    Outflow,
    OutflowSource,
    PlannedEvent,
    PlannedEventKind,
)
from api.domain.revenue import RevenueModel
from api.money import Cents, format_eur

ENGINE_VERSION = "forecast-1.0.0"
MAX_OUTFLOW_DRIVERS = 5


@dataclass(frozen=True, slots=True)
class ForecastInput:
    as_of: date
    start_balance: Cents
    cash_floor: Cents
    revenue_model: RevenueModel
    outflows: Sequence[Outflow] = ()
    inflows: Sequence[Inflow] = ()
    planned_events: Sequence[PlannedEvent] = ()
    credit_lines: Sequence[CreditLine] = ()
    horizon_days: int = 91


@dataclass(frozen=True, slots=True)
class ForecastDay:
    """Flow columns are signed: closing = previous closing + their sum."""

    date: date
    revenue: Cents
    variable_costs: Cents
    fixed_outflows: Cents
    other_inflows: Cents
    closing: Cents
    credit_available: Cents
    below_floor: bool
    closed: bool


@dataclass(frozen=True, slots=True)
class Breach:
    date: date
    closing: Cents
    shortfall: Cents


@dataclass(frozen=True, slots=True)
class DayBalance:
    date: date
    closing: Cents


@dataclass(frozen=True, slots=True)
class ForecastSummary:
    first_breach: Breach | None
    lowest: DayBalance
    days_below_floor: int
    end_balance: Cents
    headroom_at_lowest: Cents


class DriverKind(StrEnum):
    NO_REVENUE = "NO_REVENUE"
    OUTFLOW = "OUTFLOW"
    MISSING_INFLOW = "MISSING_INFLOW"


@dataclass(frozen=True, slots=True)
class Driver:
    kind: DriverKind
    label: str
    amount: Cents
    date_from: date
    date_to: date
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class Assumption:
    code: str
    text: str


@dataclass(frozen=True, slots=True)
class Forecast:
    as_of: date
    horizon_days: int
    start_balance: Cents
    cash_floor: Cents
    days: tuple[ForecastDay, ...]
    summary: ForecastSummary
    drivers: tuple[Driver, ...]
    assumptions: tuple[Assumption, ...]
    planned_events: tuple[PlannedEvent, ...] = field(default=())
    credit_lines: tuple[CreditLine, ...] = field(default=())
    engine_version: str = ENGINE_VERSION


def compute_forecast(data: ForecastInput) -> Forecast:
    if data.horizon_days < 1:
        raise ValueError("horizon_days must be at least 1")
    end = window_end(data.as_of, data.horizon_days)

    outflows_by_day: dict[date, Cents] = defaultdict(int)
    for outflow in data.outflows:
        outflows_by_day[outflow.scheduled_date] -= outflow.amount
    inflows_by_day: dict[date, Cents] = defaultdict(int)
    for inflow in data.inflows:
        inflows_by_day[inflow.expected_date] += inflow.amount
    closures = [e for e in data.planned_events if e.kind is PlannedEventKind.CLOSURE]

    rows: list[ForecastDay] = []
    closing = data.start_balance
    for day in days(data.as_of, data.horizon_days):
        closure = next((e for e in closures if e.covers(day)), None)
        expected = data.revenue_model.expected(day)
        revenue = 0 if closure else expected.revenue
        variable_costs = 0 if closure and closure.variable_costs_stop else expected.variable_costs
        fixed = outflows_by_day[day]
        other = inflows_by_day[day]
        closing += revenue + variable_costs + fixed + other
        credit = sum(line.available_on(day) for line in data.credit_lines)
        rows.append(
            ForecastDay(
                date=day,
                revenue=revenue,
                variable_costs=variable_costs,
                fixed_outflows=fixed,
                other_inflows=other,
                closing=closing,
                credit_available=credit,
                below_floor=closing + credit < data.cash_floor,
                closed=closure is not None,
            )
        )

    summary = _summarize(rows, data.cash_floor)
    return Forecast(
        as_of=data.as_of,
        horizon_days=data.horizon_days,
        start_balance=data.start_balance,
        cash_floor=data.cash_floor,
        days=tuple(rows),
        summary=summary,
        drivers=tuple(_drivers(data, closures, end, summary.first_breach)),
        assumptions=tuple(_assumptions(data, closures)),
        planned_events=tuple(data.planned_events),
        credit_lines=tuple(data.credit_lines),
    )


def _summarize(rows: list[ForecastDay], cash_floor: Cents) -> ForecastSummary:
    breaches = [r for r in rows if r.below_floor]
    first = breaches[0] if breaches else None
    lowest = min(rows, key=lambda r: r.closing)
    return ForecastSummary(
        first_breach=Breach(
            date=first.date,
            closing=first.closing,
            shortfall=cash_floor - (first.closing + first.credit_available),
        )
        if first
        else None,
        lowest=DayBalance(date=lowest.date, closing=lowest.closing),
        days_below_floor=len(breaches),
        end_balance=rows[-1].closing,
        headroom_at_lowest=lowest.closing + lowest.credit_available - cash_floor,
    )


def _drivers(
    data: ForecastInput,
    closures: list[PlannedEvent],
    end: date,
    first_breach: Breach | None,
) -> list[Driver]:
    drivers = []
    for closure in closures:
        start, stop = max(closure.date_from, data.as_of), min(closure.date_to, end)
        if start > stop:
            continue
        lost = sum(
            data.revenue_model.expected(d).revenue for d in days(start, (stop - start).days + 1)
        )
        drivers.append(
            Driver(
                kind=DriverKind.NO_REVENUE,
                label=f"Closed {start:%d %b} – {stop:%d %b}: no card revenue ({format_eur(lost)})",
                amount=lost,
                date_from=start,
                date_to=stop,
            )
        )
    if first_breach is None:
        return drivers

    for inflow in data.inflows:
        if inflow.original_expected_date <= first_breach.date < inflow.expected_date:
            drivers.append(
                Driver(
                    kind=DriverKind.MISSING_INFLOW,
                    ref=inflow.ref,
                    label=f"{inflow.counterparty} now expected {inflow.expected_date:%d %b}",
                    amount=inflow.amount,
                    date_from=inflow.original_expected_date,
                    date_to=inflow.expected_date,
                )
            )

    before_breach = [
        o for o in data.outflows if data.as_of <= o.scheduled_date <= first_breach.date
    ]
    for outflow in sorted(before_breach, key=lambda o: (-o.amount, o.scheduled_date, o.ref))[
        :MAX_OUTFLOW_DRIVERS
    ]:
        drivers.append(
            Driver(
                kind=DriverKind.OUTFLOW,
                ref=outflow.ref,
                label=f"{outflow.description or outflow.counterparty} on "
                f"{outflow.scheduled_date:%d %b}",
                amount=outflow.amount,
                date_from=outflow.scheduled_date,
                date_to=outflow.scheduled_date,
            )
        )
    return drivers


def _assumptions(data: ForecastInput, closures: list[PlannedEvent]) -> list[Assumption]:
    model = data.revenue_model
    result = [
        Assumption(
            code=f"REVENUE_WEEKDAY_AVERAGE_{model.weeks}W",
            text=f"Daily card revenue and variable costs are the weekday averages of "
            f"{model.window_from:%d %b} – {model.window_to:%d %b}, with no seasonal uplift.",
        ),
        Assumption(
            code="CALENDAR_DAYS",
            text="Every calendar day counts, because the bank books payments on weekends too.",
        ),
        Assumption(
            code="CLOSING_DAY_CONVENTION",
            text="Payments on the same day are netted at end of day.",
        ),
    ]
    for currency in sorted({o.original_currency for o in data.outflows if o.original_currency}):
        result.append(
            Assumption(
                code=f"{currency}_AT_LAST_RATE",
                text=f"Future {currency} payments are converted at the last observed rate.",
            )
        )
    if any(o.source is OutflowSource.OWNER for o in data.outflows):
        result.append(
            Assumption(
                code="OWNER_SCHEDULE",
                text="Payments not yet visible in the statement come from the owner's schedule.",
            )
        )
    if any(c.variable_costs_stop for c in closures):
        result.append(
            Assumption(
                code="CLOSURE_STOPS_VARIABLE_COSTS",
                text="While closed there is no card revenue and no supplier or market spending.",
            )
        )
    return result
