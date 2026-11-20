from dataclasses import dataclass
from datetime import date
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
    amount: Cents  # EUR
    category: Category
    original_amount: Cents | None = None
    original_currency: str | None = None
    fx_rate: str | None = None


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
    amount: Cents  # EUR estimate for the next payment
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
    recurring_id: UUID | None = None
    original_amount: Cents | None = None
    original_currency: str | None = None
    fx_rate: str | None = None
    version: int = 1
