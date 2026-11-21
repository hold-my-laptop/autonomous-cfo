from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from api.money import Cents


class Category(StrEnum):
    CARD_REVENUE = "CARD_REVENUE"
    VARIABLE_COST = "VARIABLE_COST"
    RENT = "RENT"
    PAYROLL = "PAYROLL"
    SOCIAL_SECURITY = "SOCIAL_SECURITY"
    TAX = "TAX"
    UTILITIES = "UTILITIES"
    PARTNER_TRANSFER = "PARTNER_TRANSFER"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class Transaction:
    id: UUID
    account: str
    booking_date: date
    value_date: date
    type: str
    counterparty: str
    counterparty_account: str
    reference: str
    amount: Cents
    category: Category
    original_amount: Cents | None = None
    original_currency: str | None = None
    fx_rate: str | None = None


@dataclass(frozen=True, slots=True)
class Company:
    name: str
    owner: str
    currency: str
    account: str


class MandateStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class Mandate:
    """The owner's rules for what Vero may do alone (one version)."""

    version: int
    status: MandateStatus
    cash_floor: Cents
    act_alone_enabled: bool
    max_delay_days: int
    max_amount_per_action: Cents
    max_amount_per_day: Cents
    protected_categories: frozenset[Category]
    credit_enabled: bool
    max_credit_amount: Cents
    paused: bool
    created_at: datetime
    activated_at: datetime | None = None
    note: str | None = None


class PlannedEventKind(StrEnum):
    CLOSURE = "CLOSURE"


@dataclass(frozen=True, slots=True)
class PlannedEvent:
    id: UUID
    kind: PlannedEventKind
    date_from: date
    date_to: date
    variable_costs_stop: bool = True
    note: str | None = None

    def __post_init__(self) -> None:
        if self.date_to < self.date_from:
            raise ValueError("date_to must not be before date_from")

    def covers(self, day: date) -> bool:
        return self.date_from <= day <= self.date_to


@dataclass(frozen=True, slots=True)
class Inflow:
    """A known incoming payment other than card revenue. `amount` is positive."""

    id: UUID
    ref: str
    counterparty: str
    amount: Cents
    expected_date: date
    original_expected_date: date


@dataclass(frozen=True, slots=True)
class CreditLine:
    """Approved headroom, available from `available_from` to `available_until` inclusive."""

    amount: Cents
    available_from: date
    available_until: date

    def available_on(self, day: date) -> Cents:
        return self.amount if self.available_from <= day <= self.available_until else 0


class OutflowSource(StrEnum):
    DETECTED = "DETECTED"
    OWNER = "OWNER"


@dataclass(frozen=True, slots=True)
class RecurringPayment:
    """A monthly payment detected in the statement history. Amounts are positive."""

    id: UUID
    ref_prefix: str
    counterparty: str
    category: Category
    day_of_month: int
    amount: Cents
    last_seen: date
    occurrences: int
    original_amount: Cents | None = None
    original_currency: str | None = None
    fx_rate: str | None = None


@dataclass(frozen=True, slots=True)
class Outflow:
    """A known future payment. `amount` is positive (money going out)."""

    id: UUID
    ref: str
    counterparty: str
    category: Category
    amount: Cents
    due_date: date
    scheduled_date: date
    latest_allowed_date: date | None
    source: OutflowSource
    description: str | None = None
    recurring_id: UUID | None = None
    original_amount: Cents | None = None
    original_currency: str | None = None
    fx_rate: str | None = None
    version: int = 1


class EventType(StrEnum):
    CLOSURE_PLANNED = "CLOSURE_PLANNED"
    RESCHEDULE_REQUESTED = "RESCHEDULE_REQUESTED"


class RunKind(StrEnum):
    """Which agent a run belongs to: Vero Cash (closures, reschedules) or Vero Proof
    (documents and cost changes). Each kind has its own tools (docs/BACKEND.md P09)."""

    CASH = "CASH"
    PROOF = "PROOF"


@dataclass(frozen=True, slots=True)
class Event:
    id: UUID
    type: EventType
    payload: dict
    created_at: datetime
    financial_version_after: int


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    FINALIZING = "FINALIZING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RunMode(StrEnum):
    AGENT = "AGENT"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True, slots=True)
class TraceStep:
    """One plain-language line of what a run did. `source` is BACKEND or AGENT."""

    seq: int
    kind: str
    source: str
    label: str
    at: datetime
    tool: str | None = None
    data: dict | None = None


@dataclass(frozen=True, slots=True)
class Run:
    id: UUID
    event_id: UUID
    trigger_type: EventType
    trigger_label: str
    status: RunStatus
    mode: RunMode
    created_at: datetime
    finished_at: datetime | None = None
    forecast_id: UUID | None = None
    trace: tuple[TraceStep, ...] = ()
    recommendation_ids: tuple[UUID, ...] = ()
    headline: str | None = None
    gap_closed: bool | None = None
    started_financial_version: int | None = None
    tool_log: dict | None = None
    headline_source: str = "TEMPLATE"
    explain_request: dict | None = None
    kind: RunKind = RunKind.CASH


class RecommendationKind(StrEnum):
    RESCHEDULE_PAYMENT = "RESCHEDULE_PAYMENT"
    CREDIT_LINE = "CREDIT_LINE"


class RecommendationStatus(StrEnum):
    EXECUTED = "EXECUTED"
    PENDING = "PENDING"
    APPROVED_EXECUTED = "APPROVED_EXECUTED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class Recommendation:
    """A proposed or executed action. `details` holds JSON-ready display data: subject,
    reason, checks, impact and explanation."""

    id: UUID
    run_id: UUID
    kind: RecommendationKind
    lane: str
    status: RecommendationStatus
    created_at: datetime
    based_on_financial_version: int
    details: dict
    outflow_id: UUID | None = None
    outflow_version: int | None = None
    to_date: date | None = None
    credit_line: CreditLine | None = None
    receipt_ids: tuple[UUID, ...] = ()
    decided_at: datetime | None = None
    executed_by: str | None = None
    confirmation_ref: str | None = None
    executed_at: datetime | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Receipt:
    """Immutable evidence of a proposal (PROPOSAL) or an owner decision (DECISION)."""

    id: UUID
    recommendation_id: UUID
    kind: str
    created_at: datetime
    body: dict
