"""The demo company's current state, kept behind a repository so storage can change.

`BaseData` is derived from the bank statement and never changes while the app runs. Everything
else in `DemoState` is changed by the owner or by Vero and is what repositories persist."""

import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Protocol
from uuid import UUID, uuid5

from api.config import Settings
from api.dates import window_end
from api.domain.backtest import Backtest, compute_backtest
from api.domain.balances import balance_before
from api.domain.forecast import Forecast, ForecastInput, compute_forecast
from api.domain.importer import StatementImport, load_statement
from api.domain.models import (
    Category,
    Company,
    CreditLine,
    Event,
    Inflow,
    Mandate,
    MandateStatus,
    Outflow,
    PlannedEvent,
    RecurringPayment,
    Run,
    Transaction,
)
from api.domain.planner import PlanResult, plan_moves
from api.domain.recurring import detect_recurring, project_outflows
from api.domain.revenue import RevenueModel, build_revenue_model
from api.domain.schedule import project_scheduled, sort_outflows
from api.money import Cents
from api.seed import PIZZERIA_COMPANY, PIZZERIA_TAX_SCHEDULE, initial_mandate

_FORECAST_ID_NAMESPACE = UUID("4a9f0e2b-7c1d-4e3f-8a5b-6c7d8e9f0a1b")


class StateNotLoadedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredResponse:
    """A successful response kept for an Idempotency-Key, as JSON-compatible data."""

    request_hash: str
    response: Any


@dataclass(frozen=True)
class BaseData:
    company: Company
    as_of: date
    opening_balance: Cents
    horizon_days: int
    statement: StatementImport
    revenue_model: RevenueModel
    recurring: tuple[RecurringPayment, ...]
    outflows: tuple[Outflow, ...]
    inflows: tuple[Inflow, ...] = ()


@dataclass
class DemoState:
    company: Company
    as_of: date
    opening_balance: Cents
    horizon_days: int
    statement: StatementImport
    revenue_model: RevenueModel
    recurring: list[RecurringPayment]
    outflows: list[Outflow]
    mandates: list[Mandate]
    inflows: list[Inflow] = field(default_factory=list)
    planned_events: list[PlannedEvent] = field(default_factory=list)
    credit_lines: list[CreditLine] = field(default_factory=list)
    financial_version: int = 1
    events: list[Event] = field(default_factory=list)
    runs: dict[UUID, Run] = field(default_factory=dict)
    idempotency: dict[str, StoredResponse] = field(default_factory=dict)

    @classmethod
    def from_base(cls, base: BaseData, mandates: list[Mandate], **changes: Any) -> "DemoState":
        return cls(
            company=base.company,
            as_of=base.as_of,
            opening_balance=base.opening_balance,
            horizon_days=base.horizon_days,
            statement=base.statement,
            revenue_model=base.revenue_model,
            recurring=list(base.recurring),
            outflows=list(base.outflows),
            inflows=list(base.inflows),
            mandates=mandates,
            **changes,
        )

    @property
    def history(self) -> list[Transaction]:
        """Transactions the owner already has: booked before `as_of`."""
        return [t for t in self.statement.transactions if t.booking_date < self.as_of]

    @property
    def statement_until(self) -> date:
        return max(t.booking_date for t in self.history)

    @property
    def balance(self) -> Cents:
        return balance_before(self.opening_balance, self.statement.transactions, self.as_of)

    @property
    def window_end(self) -> date:
        return window_end(self.as_of, self.horizon_days)

    @property
    def active_mandate(self) -> Mandate:
        return next(m for m in self.mandates if m.status is MandateStatus.ACTIVE)

    @property
    def forecast_id(self) -> UUID:
        """Same inputs, same id: derived from the financial and mandate versions."""
        return uuid5(
            _FORECAST_ID_NAMESPACE,
            f"{self.as_of}|{self.financial_version}|{self.active_mandate.version}",
        )

    def forecast_input(self) -> ForecastInput:
        return ForecastInput(
            as_of=self.as_of,
            start_balance=self.balance,
            cash_floor=self.active_mandate.cash_floor,
            revenue_model=self.revenue_model,
            outflows=tuple(self.outflows),
            inflows=tuple(self.inflows),
            planned_events=tuple(self.planned_events),
            credit_lines=tuple(self.credit_lines),
            horizon_days=self.horizon_days,
        )

    def forecast(self) -> Forecast:
        return compute_forecast(self.forecast_input())

    def plan(self) -> PlanResult:
        mandate = self.active_mandate
        return plan_moves(
            self.forecast_input(), mandate.protected_categories, mandate.max_delay_days
        )

    def backtest(self) -> Backtest:
        return compute_backtest(self.forecast(), self.opening_balance, self.statement.transactions)


def build_base(settings: Settings) -> BaseData:
    """Import the sample statement and derive everything Vero knows on `demo_as_of_date`."""
    statement = load_statement(settings.statement_path)
    as_of = settings.demo_as_of_date
    history = [t for t in statement.transactions if t.booking_date < as_of]
    end = window_end(as_of, settings.forecast_horizon_days)
    recurring = detect_recurring(history, as_of)
    outflows = project_outflows(
        recurring,
        as_of,
        end,
        {Category.PARTNER_TRANSFER: settings.partner_transfer_movable_days},
    ) + project_scheduled(PIZZERIA_TAX_SCHEDULE, as_of, end)
    return BaseData(
        company=PIZZERIA_COMPANY,
        as_of=as_of,
        opening_balance=settings.opening_balance,
        horizon_days=settings.forecast_horizon_days,
        statement=statement,
        revenue_model=build_revenue_model(history, as_of, settings.revenue_model_weeks),
        recurring=tuple(recurring),
        outflows=tuple(sort_outflows(outflows)),
    )


def initial_mandates(as_of: date) -> list[Mandate]:
    return [initial_mandate(datetime.combine(as_of, time(8, 0), tzinfo=UTC))]


def fresh_state(base: BaseData) -> DemoState:
    return DemoState.from_base(base, initial_mandates(base.as_of))


def build_demo_state(settings: Settings) -> DemoState:
    return fresh_state(build_base(settings))


class StateRepository(Protocol):
    def start(self) -> None:
        """Load the state at startup (seeding it if there is none)."""

    def close(self) -> None: ...

    def reset(self) -> DemoState: ...

    def read(self) -> AbstractContextManager[DemoState]: ...

    def write(self) -> AbstractContextManager[DemoState]: ...


class InMemoryStateRepository:
    """Holds one DemoState in memory. Lost on restart; reset rebuilds it from the statement."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._state: DemoState | None = None

    def start(self) -> None:
        self.reset()

    def close(self) -> None:
        return None

    def reset(self) -> DemoState:
        state = build_demo_state(self._settings)
        with self._lock:
            self._state = state
        return state

    def _current(self) -> DemoState:
        if self._state is None:
            raise StateNotLoadedError("demo state is not loaded")
        return self._state

    @contextmanager
    def read(self) -> Iterator[DemoState]:
        with self._lock:
            yield self._current()

    @contextmanager
    def write(self) -> Iterator[DemoState]:
        with self._lock:
            yield self._current()
