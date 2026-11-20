"""PostgreSQL storage (SQLModel) for the state the owner and Vero change.

Data derived from the bank statement is rebuilt at startup and kept in memory; only the
changeable part of DemoState is stored. Every write locks the company row, so concurrent
writers (or replicas) are serialized."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, BigInteger, DateTime
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
    Receipt,
    Recommendation,
    RecommendationKind,
    RecommendationStatus,
    Run,
    RunMode,
    RunStatus,
    TraceStep,
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
SCHEMA_VERSION = 3


class SchemaMetaRow(SQLModel, table=True):
    __tablename__ = "schema_meta"

    id: int = Field(default=1, primary_key=True)
    version: int


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

    id: UUID = Field(primary_key=True)
    seq: int
    event_id: UUID
    trigger_type: str
    trigger_label: str
    status: str
    mode: str
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    forecast_id: UUID | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    recommendation_ids: list[str] = Field(default_factory=list, sa_type=JSON)
    headline: str | None = None
    gap_closed: bool | None = None
    started_financial_version: int | None = None
    tool_log: dict[str, Any] | None = Field(default=None, sa_type=JSON)


class OutflowChangeRow(SQLModel, table=True):
    __tablename__ = "outflow_changes"

    outflow_id: UUID = Field(primary_key=True)
    scheduled_date: date
    version: int


class RecommendationRow(SQLModel, table=True):
    __tablename__ = "recommendations"

    id: UUID = Field(primary_key=True)
    seq: int
    run_id: UUID
    kind: str
    lane: str
    status: str
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
    based_on_financial_version: int
    details: dict[str, Any] = Field(sa_type=JSON)
    outflow_id: UUID | None = None
    outflow_version: int | None = None
    to_date: date | None = None
    credit_amount: int | None = Field(default=None, sa_type=BigInteger)
    credit_from: date | None = None
    credit_until: date | None = None
    receipt_ids: list[str] = Field(default_factory=list, sa_type=JSON)
    decided_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    executed_by: str | None = None
    confirmation_ref: str | None = None
    executed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    note: str | None = None


class ReceiptRow(SQLModel, table=True):
    __tablename__ = "receipts"

    id: UUID = Field(primary_key=True)
    seq: int
    recommendation_id: UUID
    kind: str
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
    body: dict[str, Any] = Field(sa_type=JSON)


class IdempotencyRow(SQLModel, table=True):
    __tablename__ = "idempotency_keys"

    key: str = Field(primary_key=True)
    request_hash: str
    response: Any = Field(sa_type=JSON)


_CHANGEABLE_TABLES = (
    MandateRow,
    PlannedEventRow,
    CreditLineRow,
    EventRow,
    RunRow,
    IdempotencyRow,
    OutflowChangeRow,
    RecommendationRow,
    ReceiptRow,
)
_ALL_TABLES = (SchemaMetaRow, CompanyStateRow, *_CHANGEABLE_TABLES)


def _trace_json(step: TraceStep) -> dict[str, Any]:
    return {
        "seq": step.seq,
        "kind": step.kind,
        "source": step.source,
        "label": step.label,
        "at": step.at.isoformat(),
        "tool": step.tool,
        "data": step.data,
    }


def _trace_step(data: dict[str, Any]) -> TraceStep:
    return TraceStep(
        seq=data["seq"],
        kind=data["kind"],
        source=data["source"],
        label=data["label"],
        at=datetime.fromisoformat(data["at"]),
        tool=data.get("tool"),
        data=data.get("data"),
    )


def _recommendation_row(seq: int, r: Recommendation) -> RecommendationRow:
    return RecommendationRow(
        id=r.id,
        seq=seq,
        run_id=r.run_id,
        kind=r.kind.value,
        lane=r.lane,
        status=r.status.value,
        created_at=r.created_at,
        based_on_financial_version=r.based_on_financial_version,
        details=r.details,
        outflow_id=r.outflow_id,
        outflow_version=r.outflow_version,
        to_date=r.to_date,
        credit_amount=r.credit_line.amount if r.credit_line else None,
        credit_from=r.credit_line.available_from if r.credit_line else None,
        credit_until=r.credit_line.available_until if r.credit_line else None,
        receipt_ids=[str(i) for i in r.receipt_ids],
        decided_at=r.decided_at,
        executed_by=r.executed_by,
        confirmation_ref=r.confirmation_ref,
        executed_at=r.executed_at,
        note=r.note,
    )


def _recommendation(r: RecommendationRow) -> Recommendation:
    return Recommendation(
        id=r.id,
        run_id=r.run_id,
        kind=RecommendationKind(r.kind),
        lane=r.lane,
        status=RecommendationStatus(r.status),
        created_at=r.created_at,
        based_on_financial_version=r.based_on_financial_version,
        details=r.details,
        outflow_id=r.outflow_id,
        outflow_version=r.outflow_version,
        to_date=r.to_date,
        credit_line=CreditLine(r.credit_amount, r.credit_from, r.credit_until)
        if r.credit_amount is not None
        else None,
        receipt_ids=tuple(UUID(i) for i in r.receipt_ids),
        decided_at=r.decided_at,
        executed_by=r.executed_by,
        confirmation_ref=r.confirmation_ref,
        executed_at=r.executed_at,
        note=r.note,
    )


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
    changes = {r.outflow_id: r for r in session.exec(select(OutflowChangeRow)).all()}
    recommendations = session.exec(select(RecommendationRow).order_by(RecommendationRow.seq)).all()
    receipts = session.exec(select(ReceiptRow).order_by(ReceiptRow.seq)).all()
    state = DemoState.from_base(
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
                trace=tuple(_trace_step(t) for t in r.trace),
                recommendation_ids=tuple(UUID(i) for i in r.recommendation_ids),
                headline=r.headline,
                gap_closed=r.gap_closed,
                started_financial_version=r.started_financial_version,
                tool_log=r.tool_log,
            )
            for r in runs
        },
        idempotency={r.key: StoredResponse(r.request_hash, r.response) for r in keys},
        recommendations={r.id: _recommendation(r) for r in recommendations},
        receipts={
            r.id: Receipt(r.id, r.recommendation_id, r.kind, r.created_at, r.body) for r in receipts
        },
    )
    state.outflows = [
        replace(o, scheduled_date=changes[o.id].scheduled_date, version=changes[o.id].version)
        if o.id in changes
        else o
        for o in state.outflows
    ]
    return state


def _save(session: Session, state: DemoState) -> None:
    session.merge(
        CompanyStateRow(
            id=COMPANY_ROW_ID, as_of=state.as_of, financial_version=state.financial_version
        )
    )

    mandate_rows = {row.version: row for row in session.exec(select(MandateRow)).all()}
    desired_mandate_versions = {mandate.version for mandate in state.mandates}
    for row in mandate_rows.values():
        if row.version not in desired_mandate_versions:
            session.delete(row)
    for mandate in state.mandates:
        session.merge(_mandate_row(mandate))

    existing_planned = session.exec(select(PlannedEventRow)).all()
    desired_planned = [
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
    ]
    planned_by_id = {row.id: row for row in desired_planned}
    for row in existing_planned:
        desired = planned_by_id.get(row.id)
        if desired is None or row.seq != desired.seq:
            session.delete(row)
    for row in desired_planned:
        session.merge(row)

    existing_credit_lines = {row.seq: row for row in session.exec(select(CreditLineRow)).all()}
    desired_credit_lines = [
        CreditLineRow(
            seq=i,
            amount=c.amount,
            available_from=c.available_from,
            available_until=c.available_until,
        )
        for i, c in enumerate(state.credit_lines)
    ]
    for seq, row in existing_credit_lines.items():
        if seq >= len(desired_credit_lines):
            session.delete(row)
    for row in desired_credit_lines:
        session.merge(row)

    existing_events = session.exec(select(EventRow)).all()
    existing_event_ids = {row.id for row in existing_events}
    desired_event_ids = {event.id for event in state.events}
    for row in existing_events:
        if row.id not in desired_event_ids:
            session.delete(row)
    new_events = [
        EventRow(
            seq=i,
            id=e.id,
            type=e.type.value,
            payload=e.payload,
            created_at=e.created_at,
            financial_version_after=e.financial_version_after,
        )
        for i, e in enumerate(state.events)
        if e.id not in existing_event_ids
    ]
    session.add_all(new_events)

    _sync(
        session,
        RunRow,
        [
            RunRow(
                id=r.id,
                seq=i,
                event_id=r.event_id,
                trigger_type=r.trigger_type.value,
                trigger_label=r.trigger_label,
                status=r.status.value,
                mode=r.mode.value,
                created_at=r.created_at,
                finished_at=r.finished_at,
                forecast_id=r.forecast_id,
                trace=[_trace_json(t) for t in r.trace],
                recommendation_ids=[str(i) for i in r.recommendation_ids],
                headline=r.headline,
                gap_closed=r.gap_closed,
                started_financial_version=r.started_financial_version,
                tool_log=r.tool_log,
            )
            for i, r in enumerate(state.runs.values())
        ],
    )
    _sync(
        session,
        OutflowChangeRow,
        [
            OutflowChangeRow(outflow_id=o.id, scheduled_date=o.scheduled_date, version=o.version)
            for o in state.outflows
            if o.version > 1 or o.scheduled_date != o.due_date
        ],
    )
    _sync(
        session,
        RecommendationRow,
        [_recommendation_row(i, r) for i, r in enumerate(state.recommendations.values())],
    )
    existing_receipts = set(session.exec(select(ReceiptRow.id)).all())
    for receipt_id in existing_receipts - set(state.receipts):
        session.delete(session.get(ReceiptRow, receipt_id))
    session.add_all(
        ReceiptRow(
            id=r.id,
            seq=i,
            recommendation_id=r.recommendation_id,
            kind=r.kind,
            created_at=r.created_at,
            body=r.body,
        )
        for i, r in enumerate(state.receipts.values())
        if r.id not in existing_receipts
    )

    existing_keys = {row.key: row for row in session.exec(select(IdempotencyRow)).all()}
    for key, row in existing_keys.items():
        if key not in state.idempotency:
            session.delete(row)
    for key, value in state.idempotency.items():
        session.merge(
            IdempotencyRow(
                key=key,
                request_hash=value.request_hash,
                response=value.response,
            )
        )


def _sync(session: Session, table: type[SQLModel], desired: list[SQLModel]) -> None:
    """Make `table` contain exactly `desired`, keyed by primary key: update, insert, delete."""
    key = table.__table__.primary_key.columns.values()[0].name
    wanted = {getattr(row, key) for row in desired}
    for existing in session.exec(select(table)).all():
        if getattr(existing, key) not in wanted:
            session.delete(existing)
    for row in desired:
        session.merge(row)


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
            self._ensure_schema()
            base = self._rebuild_base()
            with Session(self._engine) as session:
                company = session.get(CompanyStateRow, COMPANY_ROW_ID)
            if self._settings.reset_on_startup or company is None or company.as_of != base.as_of:
                self.reset()
        except OperationalError as exc:
            raise StateNotLoadedError(f"database unavailable: {exc.orig}") from exc

    def close(self) -> None:
        self._engine.dispose()

    def _ensure_schema(self) -> None:
        """Create the tables; recreate them when they were made by an older SCHEMA_VERSION."""
        tables = [t.__table__ for t in _ALL_TABLES]
        SQLModel.metadata.create_all(self._engine, tables=[SchemaMetaRow.__table__])
        with Session(self._engine) as session:
            meta = session.get(SchemaMetaRow, 1)
            current = meta.version if meta else None
        if current != SCHEMA_VERSION:
            SQLModel.metadata.drop_all(self._engine, tables=tables)
            SQLModel.metadata.create_all(self._engine, tables=tables)
            with Session(self._engine) as session, session.begin():
                session.add(SchemaMetaRow(id=1, version=SCHEMA_VERSION))
        else:
            SQLModel.metadata.create_all(self._engine, tables=tables)

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
