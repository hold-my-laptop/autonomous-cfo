"""The demo company's current state, kept behind a repository so storage can change.

`BaseData` is derived from the bank statement and never changes while the app runs. Everything
else in `DemoState` is changed by the owner or by Vero and is what repositories persist."""

import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from copy import copy
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, date, datetime, time
from typing import Any, Protocol
from uuid import UUID, uuid5

from api.audit import AuditActor, AuditEntry, record
from api.config import Settings
from api.dates import window_end
from api.domain.backtest import Backtest, compute_backtest
from api.domain.balances import balance_before
from api.domain.credit import CreditSizing, size_credit_line
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
    Receipt,
    Recommendation,
    RecurringPayment,
    Run,
    Transaction,
)
from api.domain.planner import PlanResult, apply_moves, plan_moves
from api.domain.proof.check import RoutineCheck, preloaded_findings
from api.domain.proof.cost_memory import CostItem, build_cost_memory
from api.domain.proof.dataset import ProofDataset, load_dataset
from api.domain.proof.findings import ProofFinding
from api.domain.proof.uploads import Upload
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
    proof: ProofDataset | None = None
    cost_memory: tuple[CostItem, ...] = ()
    routine_checks: tuple[RoutineCheck, ...] = ()
    preloaded_findings: tuple[ProofFinding, ...] = ()


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
    recommendations: dict[UUID, Recommendation] = field(default_factory=dict)
    receipts: dict[UUID, Receipt] = field(default_factory=dict)
    idempotency: dict[str, StoredResponse] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)
    proof: ProofDataset | None = None
    cost_memory: tuple[CostItem, ...] = ()
    routine_checks: tuple[RoutineCheck, ...] = ()
    findings: dict[UUID, ProofFinding] = field(default_factory=dict)
    uploads: dict[str, Upload] = field(default_factory=dict)
    new_files: dict[str, bytes] = field(default_factory=dict)

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
            proof=base.proof,
            cost_memory=base.cost_memory,
            routine_checks=base.routine_checks,
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

    def credit_sizing(
        self, available_until: date, rounding: Cents, apply_plan: bool = True
    ) -> CreditSizing:
        """Credit line for the remaining gap, after the planner's moves when `apply_plan`."""
        data = self.forecast_input()
        if apply_plan:
            data = apply_moves(data, self.plan().candidates)
        return size_credit_line(data, available_until, rounding)

    def backtest(self) -> Backtest:
        """Forecast accuracy against what was really booked. Vero's simulated moves and credit
        lines never happened in reality, so the forecast compared leaves them out: every
        payment on its due date, no credit."""
        unchanged = replace(
            self.forecast_input(),
            outflows=tuple(replace(o, scheduled_date=o.due_date) for o in self.outflows),
            credit_lines=(),
        )
        return compute_backtest(
            compute_forecast(unchanged), self.opening_balance, self.statement.transactions
        )


def build_base(settings: Settings, statement: StatementImport | None = None) -> BaseData:
    """Derive everything Vero knows on `demo_as_of_date` from the statement (by default the CSV
    file at `statement_path`) and the Proof dataset, which must agree with it."""
    statement = statement or load_statement(settings.statement_path)
    as_of = settings.demo_as_of_date
    proof = load_dataset(settings.proof_dataset_path, statement, as_of)
    history = [t for t in statement.transactions if t.booking_date < as_of]
    end = window_end(as_of, settings.forecast_horizon_days)
    recurring = detect_recurring(history, as_of)
    cost_memory = build_cost_memory(recurring, history, proof, as_of)
    findings, routine = preloaded_findings(proof, cost_memory, history, demo_load_time(as_of))
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
        proof=proof,
        cost_memory=cost_memory,
        routine_checks=routine,
        preloaded_findings=findings,
    )


def demo_load_time(as_of: date) -> datetime:
    """When the demo data counts as loaded: 08:00 UTC on `as_of`, fixed so resets repeat."""
    return datetime.combine(as_of, time(8, 0), tzinfo=UTC)


def initial_mandates(as_of: date) -> list[Mandate]:
    return [initial_mandate(demo_load_time(as_of))]


def fresh_state(base: BaseData) -> DemoState:
    """The demo company as loaded, with a new activity log that starts at the load."""
    mandates = initial_mandates(base.as_of)
    state = DemoState.from_base(base, mandates, findings={f.id: f for f in base.preloaded_findings})
    record(
        state,
        mandates[0].created_at,
        AuditActor.SYSTEM,
        f"Loaded {base.company.name}: {len(state.history)} statement lines up to "
        f"{state.statement_until:%d %b %Y}, mandate v1 active",
    )
    return state


def build_demo_state(settings: Settings) -> DemoState:
    return fresh_state(build_base(settings))


class StateRepository(Protocol):
    def start(self) -> None:
        """Load the state at startup (seeding it if there is none)."""

    def close(self) -> None: ...

    def reset(self) -> DemoState: ...

    def read(self) -> AbstractContextManager[DemoState]: ...

    def write(self) -> AbstractContextManager[DemoState]: ...

    def read_file(self, document_id: str) -> bytes | None:
        """The bytes of an uploaded PDF, or None."""


class InMemoryStateRepository:
    """Holds one DemoState in memory. Lost on restart; reset rebuilds it from the statement."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._state: DemoState | None = None
        self._files: dict[str, bytes] = {}

    def start(self) -> None:
        self.reset()

    def close(self) -> None:
        return None

    def reset(self) -> DemoState:
        state = build_demo_state(self._settings)
        with self._lock:
            self._state = state
            self._files = {}
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
        """Like a database transaction: if the block raises, the state is put back as it was.

        Items are immutable and only the lists and dicts holding them change, so copying the
        containers is enough for the snapshot."""
        with self._lock:
            state = self._current()
            snapshot = replace(
                state, **{f.name: copy(getattr(state, f.name)) for f in fields(state)}
            )
            try:
                yield state
            except BaseException:
                self._state = snapshot
                raise
            self._files.update(state.new_files)
            state.new_files.clear()

    def read_file(self, document_id: str) -> bytes | None:
        with self._lock:
            return self._files.get(document_id)
