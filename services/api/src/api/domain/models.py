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
