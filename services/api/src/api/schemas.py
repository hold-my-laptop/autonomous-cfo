"""Public API response models (see docs/contracts/DATA_CONTRACTS.md). Money is serialized as a
string of integer cents."""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.domain import backtest as bt
from api.domain import forecast as fc
from api.domain import models as dm
from api.domain import revenue as rv
from api.domain.models import (
    Category,
    EventType,
    MandateStatus,
    OutflowSource,
    PlannedEventKind,
    RunMode,
    RunStatus,
)
from api.money import Money
from api.state import DemoState

WEEKS_IN_FORECAST = 13


class Company(BaseModel):
    name: str
    owner: str
    currency: str
    as_of_date: date
    balance: Money
    statement_until: date
    financial_version: int
    mandate_version: int
    paused: bool

    @classmethod
    def from_state(cls, state: DemoState) -> Self:
        mandate = state.active_mandate
        return cls(
            name=state.company.name,
            owner=state.company.owner,
            currency=state.company.currency,
            as_of_date=state.as_of,
            balance=state.balance,
            statement_until=state.statement_until,
            financial_version=state.financial_version,
            mandate_version=mandate.version,
            paused=mandate.paused,
        )


class Transaction(BaseModel):
    id: UUID
    booking_date: date
    type: str
    counterparty: str
    reference: str
    amount: Money
    category: Category
    original_amount: Money | None
    original_currency: str | None
    fx_rate: str | None

    @classmethod
    def from_domain(cls, t: dm.Transaction) -> Self:
        return cls(
            id=t.id,
            booking_date=t.booking_date,
            type=t.type,
            counterparty=t.counterparty,
            reference=t.reference,
            amount=t.amount,
            category=t.category,
            original_amount=t.original_amount,
            original_currency=t.original_currency,
            fx_rate=t.fx_rate,
        )


class Outflow(BaseModel):
    id: UUID
    ref: str
    counterparty: str
    description: str | None
    category: Category
    amount: Money
    original_amount: Money | None
    original_currency: str | None
    fx_rate: str | None
    due_date: date
    scheduled_date: date
    latest_allowed_date: date | None
    protected: bool
    source: OutflowSource
    version: int

    @classmethod
    def from_domain(cls, o: dm.Outflow, mandate: dm.Mandate) -> Self:
        return cls(
            id=o.id,
            ref=o.ref,
            counterparty=o.counterparty,
            description=o.description,
            category=o.category,
            amount=o.amount,
            original_amount=o.original_amount,
            original_currency=o.original_currency,
            fx_rate=o.fx_rate,
            due_date=o.due_date,
            scheduled_date=o.scheduled_date,
            latest_allowed_date=o.latest_allowed_date,
            protected=o.category in mandate.protected_categories,
            source=o.source,
            version=o.version,
        )


class Inflow(BaseModel):
    id: UUID
    ref: str
    counterparty: str
    amount: Money
    expected_date: date
    original_expected_date: date

    @classmethod
    def from_domain(cls, i: dm.Inflow) -> Self:
        return cls(
            id=i.id,
            ref=i.ref,
            counterparty=i.counterparty,
            amount=i.amount,
            expected_date=i.expected_date,
            original_expected_date=i.original_expected_date,
        )


class WeekdayAverage(BaseModel):
    weekday: Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    revenue: Money
    variable_costs: Money


class RevenueModel(BaseModel):
    method: str
    window_from: date
    window_to: date
    weeks: int
    by_weekday: list[WeekdayAverage]
    model_version: str

    @classmethod
    def from_domain(cls, m: rv.RevenueModel) -> Self:
        return cls(
            method=m.method,
            window_from=m.window_from,
            window_to=m.window_to,
            weeks=m.weeks,
            by_weekday=[
                WeekdayAverage(weekday=w.name, revenue=w.revenue, variable_costs=w.variable_costs)
                for w in m.by_weekday
            ],
            model_version=m.model_version,
        )


class PlannedEvent(BaseModel):
    id: UUID
    kind: PlannedEventKind
    date_from: date
    date_to: date
    revenue_factor: str
    variable_costs_stop: bool
    note: str | None

    @classmethod
    def from_domain(cls, e: dm.PlannedEvent) -> Self:
        return cls(
            id=e.id,
            kind=e.kind,
            date_from=e.date_from,
            date_to=e.date_to,
            revenue_factor="0",
            variable_costs_stop=e.variable_costs_stop,
            note=e.note,
        )


class Mandate(BaseModel):
    version: int
    status: MandateStatus
    cash_floor: Money
    act_alone_enabled: bool
    max_delay_days: int
    max_amount_per_action: Money
    max_amount_per_day: Money
    protected_categories: list[Category]
    credit_enabled: bool
    max_credit_amount: Money
    paused: bool
    note: str | None
    created_at: datetime
    activated_at: datetime | None

    @classmethod
    def from_domain(cls, m: dm.Mandate) -> Self:
        return cls(
            version=m.version,
            status=m.status,
            cash_floor=m.cash_floor,
            act_alone_enabled=m.act_alone_enabled,
            max_delay_days=m.max_delay_days,
            max_amount_per_action=m.max_amount_per_action,
            max_amount_per_day=m.max_amount_per_day,
            protected_categories=sorted(m.protected_categories),
            credit_enabled=m.credit_enabled,
            max_credit_amount=m.max_credit_amount,
            paused=m.paused,
            note=m.note,
            created_at=m.created_at,
            activated_at=m.activated_at,
        )


class Breach(BaseModel):
    date: date
    closing: Money
    shortfall: Money


class DayBalance(BaseModel):
    date: date
    closing: Money


class ForecastSummary(BaseModel):
    first_breach: Breach | None
    lowest: DayBalance
    days_below_floor: int
    end_balance: Money
    headroom_at_lowest: Money

    @classmethod
    def from_domain(cls, s: fc.ForecastSummary) -> Self:
        return cls(
            first_breach=Breach(
                date=s.first_breach.date,
                closing=s.first_breach.closing,
                shortfall=s.first_breach.shortfall,
            )
            if s.first_breach
            else None,
            lowest=DayBalance(date=s.lowest.date, closing=s.lowest.closing),
            days_below_floor=s.days_below_floor,
            end_balance=s.end_balance,
            headroom_at_lowest=s.headroom_at_lowest,
        )


class ForecastDay(BaseModel):
    date: date
    revenue: Money
    variable_costs: Money
    fixed_outflows: Money
    other_inflows: Money
    closing: Money
    credit_available: Money
    below_floor: bool
    closed: bool


class Driver(BaseModel):
    kind: str
    ref: str | None
    label: str
    amount: Money
    date_from: date
    date_to: date


class Assumption(BaseModel):
    code: str
    text: str


class CreditLine(BaseModel):
    amount: Money
    available_from: date
    available_until: date

    @classmethod
    def from_domain(cls, c: dm.CreditLine) -> Self:
        return cls(
            amount=c.amount, available_from=c.available_from, available_until=c.available_until
        )


class ForecastWeek(BaseModel):
    week: int
    start_date: date
    end_date: date
    inflow: Money
    outflow: Money
    closing: Money
    lowest: Money
    any_below_floor: bool


class Forecast(BaseModel):
    id: UUID
    label: Literal["CURRENT"]
    as_of_date: date
    horizon_days: int
    start_balance: Money
    cash_floor: Money
    summary: ForecastSummary
    days: list[ForecastDay]
    drivers: list[Driver]
    assumptions: list[Assumption]
    planned_events: list[PlannedEvent]
    credit_lines: list[CreditLine]
    engine_version: str
    financial_version: int
    mandate_version: int
    weeks: list[ForecastWeek] | None = None

    @classmethod
    def from_domain(cls, f: fc.Forecast, state: DemoState, weekly: bool = False) -> Self:
        mandate_version = state.active_mandate.version
        return cls(
            id=state.forecast_id,
            label="CURRENT",
            as_of_date=f.as_of,
            horizon_days=f.horizon_days,
            start_balance=f.start_balance,
            cash_floor=f.cash_floor,
            summary=ForecastSummary.from_domain(f.summary),
            days=[
                ForecastDay(
                    date=d.date,
                    revenue=d.revenue,
                    variable_costs=d.variable_costs,
                    fixed_outflows=d.fixed_outflows,
                    other_inflows=d.other_inflows,
                    closing=d.closing,
                    credit_available=d.credit_available,
                    below_floor=d.below_floor,
                    closed=d.closed,
                )
                for d in f.days
            ],
            drivers=[
                Driver(
                    kind=d.kind,
                    ref=d.ref,
                    label=d.label,
                    amount=d.amount,
                    date_from=d.date_from,
                    date_to=d.date_to,
                )
                for d in f.drivers
            ],
            assumptions=[Assumption(code=a.code, text=a.text) for a in f.assumptions],
            planned_events=[PlannedEvent.from_domain(e) for e in f.planned_events],
            credit_lines=[CreditLine.from_domain(c) for c in f.credit_lines],
            engine_version=f.engine_version,
            financial_version=state.financial_version,
            mandate_version=mandate_version,
            weeks=weekly_buckets(f) if weekly else None,
        )


def weekly_buckets(f: fc.Forecast) -> list[ForecastWeek]:
    """Display aggregation only; the daily rows stay authoritative."""
    weeks = []
    for index in range(WEEKS_IN_FORECAST):
        chunk = f.days[index * 7 : (index + 1) * 7]
        if not chunk:
            break
        weeks.append(
            ForecastWeek(
                week=index + 1,
                start_date=chunk[0].date,
                end_date=chunk[-1].date,
                inflow=sum(d.revenue + d.other_inflows for d in chunk),
                outflow=sum(d.variable_costs + d.fixed_outflows for d in chunk),
                closing=chunk[-1].closing,
                lowest=min(d.closing for d in chunk),
                any_below_floor=any(d.below_floor for d in chunk),
            )
        )
    return weeks


class BalanceSummary(BaseModel):
    first_breach: Breach | None
    lowest: DayBalance
    days_below_floor: int
    end_balance: Money

    @classmethod
    def from_domain(cls, s: bt.BalanceSummary) -> Self:
        return cls(
            first_breach=Breach(
                date=s.first_breach.date,
                closing=s.first_breach.closing,
                shortfall=s.first_breach.shortfall,
            )
            if s.first_breach
            else None,
            lowest=DayBalance(date=s.lowest.date, closing=s.lowest.closing),
            days_below_floor=s.days_below_floor,
            end_balance=s.end_balance,
        )


class BacktestDay(BaseModel):
    date: date
    forecast: Money
    actual: Money


class Backtest(BaseModel):
    forecast_id: UUID
    complete: bool
    days: list[BacktestDay]
    forecast: BalanceSummary
    actual: BalanceSummary
    mean_abs_error: Money
    lowest_error: Money
    lowest_error_bp: int
    note: str

    @classmethod
    def from_domain(cls, b: bt.Backtest, state: DemoState) -> Self:
        return cls(
            forecast_id=state.forecast_id,
            complete=b.complete,
            days=[BacktestDay(date=d.date, forecast=d.forecast, actual=d.actual) for d in b.days],
            forecast=BalanceSummary.from_domain(b.forecast),
            actual=BalanceSummary.from_domain(b.actual),
            mean_abs_error=b.mean_abs_error,
            lowest_error=b.lowest_error,
            lowest_error_bp=b.lowest_error_bp,
            note=b.note,
        )


class ClosurePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date
    date_to: date
    variable_costs_stop: bool = True
    note: str | None = Field(default=None, max_length=200)


class ClosurePlannedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["CLOSURE_PLANNED"]
    expected_financial_version: int = Field(ge=1)
    payload: ClosurePayload


EventIn = ClosurePlannedIn


class RunAccepted(BaseModel):
    run_id: UUID
    status: RunStatus


class RunTrigger(BaseModel):
    type: EventType
    label: str


class RunResult(BaseModel):
    forecast_id: UUID


class Run(BaseModel):
    run_id: UUID
    status: RunStatus
    mode: RunMode
    trigger: RunTrigger
    trace: list[dict]
    result: RunResult | None
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_domain(cls, r: dm.Run) -> Self:
        return cls(
            run_id=r.id,
            status=r.status,
            mode=r.mode,
            trigger=RunTrigger(type=r.trigger_type, label=r.trigger_label),
            trace=[],
            result=RunResult(forecast_id=r.forecast_id) if r.forecast_id else None,
            created_at=r.created_at,
            finished_at=r.finished_at,
        )
