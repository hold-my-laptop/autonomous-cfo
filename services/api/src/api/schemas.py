"""Public API response models (see docs/contracts/DATA_CONTRACTS.md). Money is serialized as a
string of integer cents."""

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from api import audit
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
    RecommendationKind,
    RecommendationStatus,
    RunMode,
    RunStatus,
)
from api.money import Money
from api.state import DemoState

WEEKS_IN_FORECAST = 13


def _public_status(status: object) -> object:
    """A run waiting for its texts is still running for the frontend."""
    return RunStatus.RUNNING.value if status == RunStatus.FINALIZING else status


PublicRunStatus = Annotated[
    Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"], BeforeValidator(_public_status)
]


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
    status: PublicRunStatus


class RunTrigger(BaseModel):
    type: EventType
    label: str


class Explanation(BaseModel):
    text: str
    source: Literal["MODEL", "REPLAY", "TEMPLATE"]


class RunCounts(BaseModel):
    done_within_mandate: int
    needs_approval: int
    not_allowed: int


class RunResult(BaseModel):
    forecast_id: UUID
    recommendation_ids: list[UUID]
    headline: Explanation | None
    counts: RunCounts
    gap_closed: bool | None


class TraceStep(BaseModel):
    seq: int
    kind: str
    source: str
    label: str
    tool: str | None
    data: dict | None
    at: datetime


class Run(BaseModel):
    run_id: UUID
    status: PublicRunStatus
    mode: RunMode
    trigger: RunTrigger
    trace: list[TraceStep]
    result: RunResult | None
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_domain(cls, r: dm.Run, state: DemoState) -> Self:
        recs = [
            state.recommendations[i] for i in r.recommendation_ids if i in state.recommendations
        ]
        return cls(
            run_id=r.id,
            status=r.status,
            mode=r.mode,
            trigger=RunTrigger(type=r.trigger_type, label=r.trigger_label),
            trace=[
                TraceStep(
                    seq=t.seq,
                    kind=t.kind,
                    source=t.source,
                    label=t.label,
                    tool=t.tool,
                    data=t.data,
                    at=t.at,
                )
                for t in r.trace
            ],
            result=RunResult(
                forecast_id=r.forecast_id,
                recommendation_ids=list(r.recommendation_ids),
                headline=Explanation(text=r.headline, source=r.headline_source)
                if r.headline
                else None,
                counts=RunCounts(
                    done_within_mandate=sum(x.lane == "DONE_WITHIN_MANDATE" for x in recs),
                    needs_approval=sum(x.lane == "NEEDS_APPROVAL" for x in recs),
                    not_allowed=sum(x.lane == "NOT_ALLOWED" for x in recs),
                ),
                gap_closed=r.gap_closed,
            )
            if r.forecast_id
            else None,
            created_at=r.created_at,
            finished_at=r.finished_at,
        )


class Check(BaseModel):
    rule: str
    result: Literal["PASS", "FAIL", "INFO"]
    effect: Literal["NEEDS_APPROVAL", "NOT_ALLOWED"] | None
    text: str


class ImpactState(BaseModel):
    first_breach: date | None
    lowest: Money
    lowest_date: date
    days_below_floor: int


class Impact(BaseModel):
    before: ImpactState
    after_if_executed: ImpactState


class RescheduleSubject(BaseModel):
    kind: Literal["RESCHEDULE_PAYMENT"]
    outflow_id: UUID
    ref: str
    counterparty: str
    category: Category
    amount: Money
    original_amount: Money | None
    original_currency: str | None
    from_date: date
    to_date: date


class CreditLineSubject(BaseModel):
    kind: Literal["CREDIT_LINE"]
    product: Literal["SHORT_TERM_CREDIT_LINE"]
    amount: Money
    available_from: date
    available_until: date
    shortfall: Money
    rounded_to: Money
    headroom_at_lowest: Money
    illustrative: Literal[True]


class Reason(BaseModel):
    first_breach: date | None
    shortfall: Money
    requested_by: Literal["OWNER"] | None = None


class Execution(BaseModel):
    execution: Literal["SIMULATED"]
    confirmation_ref: str
    executed_by: Literal["VERO", "OWNER"]
    executed_at: datetime


class Recommendation(BaseModel):
    id: UUID
    run_id: UUID
    kind: RecommendationKind
    lane: Literal["DONE_WITHIN_MANDATE", "NEEDS_APPROVAL", "NOT_ALLOWED"]
    status: RecommendationStatus
    execution: Literal["SIMULATED"]
    subject: RescheduleSubject | CreditLineSubject = Field(discriminator="kind")
    reason: Reason
    checks: list[Check]
    impact: Impact
    explanation: Explanation
    receipt_ids: list[UUID]
    executed: Execution | None
    note: str | None
    created_at: datetime
    decided_at: datetime | None

    @classmethod
    def from_domain(cls, r: dm.Recommendation) -> Self:
        d = r.details
        return cls(
            id=r.id,
            run_id=r.run_id,
            kind=r.kind,
            lane=r.lane,
            status=r.status,
            execution="SIMULATED",
            subject=d["subject"],
            reason=d["reason"],
            checks=d["checks"],
            impact=d["impact"],
            explanation=d["explanation"],
            receipt_ids=list(r.receipt_ids),
            executed=Execution(
                execution="SIMULATED",
                confirmation_ref=r.confirmation_ref,
                executed_by=r.executed_by,
                executed_at=r.executed_at,
            )
            if r.executed_at
            else None,
            note=r.note,
            created_at=r.created_at,
            decided_at=r.decided_at,
        )


class Receipt(BaseModel):
    id: UUID
    recommendation_id: UUID
    kind: Literal["PROPOSAL", "DECISION"]
    created_at: datetime
    trigger: str
    subject: RescheduleSubject | CreditLineSubject = Field(discriminator="kind")
    lane: str
    decision: Literal["APPROVED", "REJECTED", "REQUESTED"] | None
    checks: list[Check]
    before: ImpactState
    after: ImpactState
    assumptions: list[str]
    mandate_version: int
    financial_version: int
    versions: dict[str, str]
    execution: Literal["SIMULATED"]
    explanation: Explanation

    @classmethod
    def from_domain(cls, r: dm.Receipt) -> Self:
        return cls(
            id=r.id,
            recommendation_id=r.recommendation_id,
            kind=r.kind,
            created_at=r.created_at,
            **r.body,
        )


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class DecisionResult(BaseModel):
    recommendation: Recommendation
    forecast_summary: ForecastSummary
    receipt_id: UUID
    follow_up_recommendation_ids: list[UUID]


class RescheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_date: date
    note: str | None = Field(default=None, max_length=200)


class ModelHealth(BaseModel):
    ok: bool
    model: str
    mode: Literal["live", "replay", "stub"] | None = None
    latency_ms: int
    error: str | None = None


class Finding(BaseModel):
    kind: Literal["DRIVER", "RECURRING", "FX_EXPOSURE", "BACKTEST"]
    text: str


class Lanes(BaseModel):
    ANALYSED: list[Finding]
    DONE_WITHIN_MANDATE: list[Recommendation]
    NEEDS_APPROVAL: list[Recommendation]
    NOT_ALLOWED: list[Recommendation]


class RunRef(BaseModel):
    run_id: UUID
    status: PublicRunStatus


class Overview(BaseModel):
    company: Company
    cash_floor: Money
    forecast_summary: ForecastSummary
    headline: Explanation
    lanes: Lanes
    pending_count: int
    latest_run: RunRef | None


class MandateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_floor: Money
    act_alone_enabled: bool
    max_delay_days: int = Field(ge=0, le=90)
    max_amount_per_action: Money = Field(ge=0)
    max_amount_per_day: Money = Field(ge=0)
    protected_categories: list[Category]
    credit_enabled: bool
    max_credit_amount: Money = Field(ge=0)
    note: str | None = Field(default=None, max_length=200)


class MandateActivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_active_version: int = Field(ge=1)


class AuditEntry(BaseModel):
    """One sealed line of the activity log. `hash` is the SHA-256 (hex) of
    `prev_hash|seq|timestamp|actor|agent|run_id|recommendation_id|lane|execution|model|action`,
    with absent values as empty text; the first entry's `prev_hash` is 64 zeros."""

    seq: int
    timestamp: str = Field(description="UTC with milliseconds, exactly as hashed")
    actor: Literal["AGENT", "RULES", "OWNER", "SYSTEM"]
    agent: Literal["CASH"] | None
    action: str
    run_id: UUID | None
    recommendation_id: UUID | None
    lane: str | None
    execution: Literal["SIMULATED"] | None
    model: str | None
    prev_hash: str
    hash: str

    @classmethod
    def from_domain(cls, e: audit.AuditEntry) -> Self:
        return cls(
            seq=e.seq,
            timestamp=e.timestamp,
            actor=e.actor.value,
            agent=e.agent.value if e.agent else None,
            action=e.action,
            run_id=e.run_id,
            recommendation_id=e.recommendation_id,
            lane=e.lane,
            execution=e.execution,
            model=e.model,
            prev_hash=e.prev_hash,
            hash=e.hash,
        )
