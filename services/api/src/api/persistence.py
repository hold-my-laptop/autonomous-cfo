"""PostgreSQL storage (SQLModel) for the state the owner and Vero change.

Data derived from the bank statement is rebuilt at startup and kept in memory; only the
changeable part of DemoState is stored. Every write locks the company row, so concurrent
writers (or replicas) are serialized."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, BigInteger, DateTime, delete
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlmodel import Field, Session, SQLModel, create_engine, select

from api.config import Settings
from api.domain.models import (
    Category,
    CreditLine,
    Event,
    EventType,
    Mandate,
    MandateStatus,
    PlannedEvent,
    PlannedEventKind,
    Run,
    RunMode,
    RunStatus,
)
from api.state import (
    BaseData,
    DemoState,
    StateNotLoadedError,
    StoredResponse,
    build_base,
    fresh_state,
)

COMPANY_ROW_ID = 1


class CompanyStateRow(SQLModel, table=True):
    __tablename__ = "company_state"

    id: int = Field(default=COMPANY_ROW_ID, primary_key=True)
    as_of: date
    financial_version: int


class MandateRow(SQLModel, table=True):
    __tablename__ = "mandates"

    version: int = Field(primary_key=True)
    status: str
    cash_floor: int = Field(sa_type=BigInteger)
    act_alone_enabled: bool
    max_delay_days: int
    max_amount_per_action: int = Field(sa_type=BigInteger)
    max_amount_per_day: int = Field(sa_type=BigInteger)
    protected_categories: list[str] = Field(sa_type=JSON)
    credit_enabled: bool
    max_credit_amount: int = Field(sa_type=BigInteger)
    paused: bool
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
    activated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    note: str | None = None


class PlannedEventRow(SQLModel, table=True):
    __tablename__ = "planned_events"

    seq: int = Field(primary_key=True)
    id: UUID = Field(unique=True)
    kind: str
    date_from: date
    date_to: date
    variable_costs_stop: bool
    note: str | None = None


class CreditLineRow(SQLModel, table=True):
    __tablename__ = "credit_lines"

    seq: int = Field(primary_key=True)
    amount: int = Field(sa_type=BigInteger)
    available_from: date
    available_until: date


class EventRow(SQLModel, table=True):
    __tablename__ = "events"

    seq: int = Field(primary_key=True)
    id: UUID = Field(unique=True)
    type: str
    payload: dict[str, Any] = Field(sa_type=JSON)
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
    financial_version_after: int


class RunRow(SQLModel, table=True):
    __tablename__ = "runs"

    seq: int = Field(primary_key=True)
    id: UUID = Field(unique=True)
    event_id: UUID
    trigger_type: str
    trigger_label: str
    status: str
    mode: str
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    forecast_id: UUID | None = None


class IdempotencyRow(SQLModel, table=True):
    __tablename__ = "idempotency_keys"

    key: str = Field(primary_key=True)
    request_hash: str
    response: Any = Field(sa_type=JSON)


_CHANGEABLE_TABLES = (MandateRow, PlannedEventRow, CreditLineRow, EventRow, RunRow, IdempotencyRow)


def _mandate_row(m: Mandate) -> MandateRow:
    return MandateRow(
        version=m.version,
        status=m.status.value,
        cash_floor=m.cash_floor,
        act_alone_enabled=m.act_alone_enabled,
        max_delay_days=m.max_delay_days,
        max_amount_per_action=m.max_amount_per_action,
        max_amount_per_day=m.max_amount_per_day,
        protected_categories=sorted(c.value for c in m.protected_categories),
        credit_enabled=m.credit_enabled,
        max_credit_amount=m.max_credit_amount,
        paused=m.paused,
        created_at=m.created_at,
        activated_at=m.activated_at,
        note=m.note,
    )


def _mandate(r: MandateRow) -> Mandate:
    return Mandate(
        version=r.version,
        status=MandateStatus(r.status),
        cash_floor=r.cash_floor,
        act_alone_enabled=r.act_alone_enabled,
        max_delay_days=r.max_delay_days,
        max_amount_per_action=r.max_amount_per_action,
        max_amount_per_day=r.max_amount_per_day,
        protected_categories=frozenset(Category(c) for c in r.protected_categories),
        credit_enabled=r.credit_enabled,
        max_credit_amount=r.max_credit_amount,
        paused=r.paused,
        created_at=r.created_at,
        activated_at=r.activated_at,
        note=r.note,
    )


def _load(session: Session, base: BaseData, company: CompanyStateRow) -> DemoState:
    mandates = session.exec(select(MandateRow).order_by(MandateRow.version)).all()
    planned = session.exec(select(PlannedEventRow).order_by(PlannedEventRow.seq)).all()
    lines = session.exec(select(CreditLineRow).order_by(CreditLineRow.seq)).all()
    events = session.exec(select(EventRow).order_by(EventRow.seq)).all()
    runs = session.exec(select(RunRow).order_by(RunRow.seq)).all()
    keys = session.exec(select(IdempotencyRow)).all()
    return DemoState.from_base(
        base,
        [_mandate(r) for r in mandates],
        planned_events=[
            PlannedEvent(
                id=r.id,
                kind=PlannedEventKind(r.kind),
                date_from=r.date_from,
                date_to=r.date_to,
                variable_costs_stop=r.variable_costs_stop,
                note=r.note,
            )
            for r in planned
        ],
        credit_lines=[CreditLine(r.amount, r.available_from, r.available_until) for r in lines],
        financial_version=company.financial_version,
        events=[
            Event(
                id=r.id,
                type=EventType(r.type),
                payload=r.payload,
                created_at=r.created_at,
                financial_version_after=r.financial_version_after,
            )
            for r in events
        ],
        runs={
            r.id: Run(
                id=r.id,
                event_id=r.event_id,
                trigger_type=EventType(r.trigger_type),
                trigger_label=r.trigger_label,
                status=RunStatus(r.status),
                mode=RunMode(r.mode),
                created_at=r.created_at,
                finished_at=r.finished_at,
                forecast_id=r.forecast_id,
            )
            for r in runs
        },
        idempotency={r.key: StoredResponse(r.request_hash, r.response) for r in keys},
    )


def _save(session: Session, state: DemoState) -> None:
    session.merge(
        CompanyStateRow(
            id=COMPANY_ROW_ID, as_of=state.as_of, financial_version=state.financial_version
        )
    )
    for table in _CHANGEABLE_TABLES:
        session.exec(delete(table))
    session.add_all([_mandate_row(m) for m in state.mandates])
    session.add_all(
        PlannedEventRow(
            seq=i,
            id=e.id,
            kind=e.kind.value,
            date_from=e.date_from,
            date_to=e.date_to,
            variable_costs_stop=e.variable_costs_stop,
            note=e.note,
        )
        for i, e in enumerate(state.planned_events)
    )
    session.add_all(
        CreditLineRow(
            seq=i,
            amount=c.amount,
            available_from=c.available_from,
            available_until=c.available_until,
        )
        for i, c in enumerate(state.credit_lines)
    )
    session.add_all(
        EventRow(
            seq=i,
            id=e.id,
            type=e.type.value,
            payload=e.payload,
            created_at=e.created_at,
            financial_version_after=e.financial_version_after,
        )
        for i, e in enumerate(state.events)
    )
    session.add_all(
        RunRow(
            seq=i,
            id=r.id,
            event_id=r.event_id,
            trigger_type=r.trigger_type.value,
            trigger_label=r.trigger_label,
            status=r.status.value,
            mode=r.mode.value,
            created_at=r.created_at,
            finished_at=r.finished_at,
            forecast_id=r.forecast_id,
        )
        for i, r in enumerate(state.runs.values())
    )
    session.add_all(
        IdempotencyRow(key=k, request_hash=v.request_hash, response=v.response)
        for k, v in state.idempotency.items()
    )


def _lock_company(session: Session) -> CompanyStateRow | None:
    query = select(CompanyStateRow).where(CompanyStateRow.id == COMPANY_ROW_ID).with_for_update()
    return session.exec(query).first()


class PostgresStateRepository:
    def __init__(self, settings: Settings, engine: Engine | None = None) -> None:
        if engine is None and not settings.sqlalchemy_url:
            raise ValueError("DATABASE_URL is required for the PostgreSQL repository")
        self._settings = settings
        self._engine = engine or create_engine(settings.sqlalchemy_url, pool_pre_ping=True)
        self._base: BaseData | None = None
        self._base_lock = threading.Lock()

    def start(self) -> None:
        try:
            SQLModel.metadata.create_all(
                self._engine, tables=[t.__table__ for t in (CompanyStateRow, *_CHANGEABLE_TABLES)]
            )
            base = self._rebuild_base()
            with Session(self._engine) as session:
                company = session.get(CompanyStateRow, COMPANY_ROW_ID)
            if self._settings.reset_on_startup or company is None or company.as_of != base.as_of:
                self.reset()
        except OperationalError as exc:
            raise StateNotLoadedError(f"database unavailable: {exc.orig}") from exc

    def close(self) -> None:
        self._engine.dispose()

    def _rebuild_base(self) -> BaseData:
        base = build_base(self._settings)
        with self._base_lock:
            self._base = base
        return base

    def _require_base(self) -> BaseData:
        if self._base is None:
            raise StateNotLoadedError("demo state is not loaded")
        return self._base

    def reset(self) -> DemoState:
        state = fresh_state(self._rebuild_base())
        try:
            with Session(self._engine) as session, session.begin():
                _lock_company(session)
                _save(session, state)
        except OperationalError as exc:
            raise StateNotLoadedError(f"database unavailable: {exc.orig}") from exc
        return state

    @contextmanager
    def read(self) -> Iterator[DemoState]:
        base = self._require_base()
        with Session(self._engine) as session:
            try:
                company = session.get(CompanyStateRow, COMPANY_ROW_ID)
                if company is None:
                    raise StateNotLoadedError("demo state is not seeded")
                state = _load(session, base, company)
            except OperationalError as exc:
                raise StateNotLoadedError(f"database unavailable: {exc.orig}") from exc
            yield state

    @contextmanager
    def write(self) -> Iterator[DemoState]:
        base = self._require_base()
        with Session(self._engine) as session:
            try:
                session.begin()
                company = _lock_company(session)
                if company is None:
                    raise StateNotLoadedError("demo state is not seeded")
                state = _load(session, base, company)
            except OperationalError as exc:
                session.rollback()
                raise StateNotLoadedError(f"database unavailable: {exc.orig}") from exc
            try:
                yield state
                _save(session, state)
                session.commit()
            except BaseException:
                session.rollback()
                raise
