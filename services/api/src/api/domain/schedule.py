"""Payments the owner tells Vero about (e.g. the quarterly tax prepayment schedule)."""

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid5

from api.dates import months_between
from api.domain.models import Category, Outflow, OutflowSource
from api.money import Cents

_ID_NAMESPACE = UUID("8b3e1f60-2a4d-4c7e-9f1a-5d6c7b8a9e0f")


@dataclass(frozen=True, slots=True)
class ScheduledPayment:
    ref_prefix: str
    counterparty: str
    description: str
    category: Category
    amount: Cents
    day_of_month: int
    months: frozenset[int]

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError("scheduled amount must be positive")
        if not 1 <= self.day_of_month <= 31:
            raise ValueError("day_of_month must be 1-31")
        if not self.months or not self.months <= set(range(1, 13)):
            raise ValueError("months must be within 1-12")


def project_scheduled(
    payments: Iterable[ScheduledPayment], start: date, end: date
) -> list[Outflow]:
    """Owner-entered payments due between `start` and `end` (inclusive). Never movable."""
    outflows = []
    for payment in payments:
        for year, month in months_between(start, end):
            if month not in payment.months:
                continue
            due = date(year, month, min(payment.day_of_month, calendar.monthrange(year, month)[1]))
            if not start <= due <= end:
                continue
            outflows.append(
                Outflow(
                    id=uuid5(_ID_NAMESPACE, f"{payment.ref_prefix}|{due.isoformat()}"),
                    ref=f"{payment.ref_prefix}-{month:02d}",
                    counterparty=payment.counterparty,
                    description=payment.description,
                    category=payment.category,
                    amount=payment.amount,
                    due_date=due,
                    scheduled_date=due,
                    latest_allowed_date=None,
                    source=OutflowSource.OWNER,
                )
            )
    return sort_outflows(outflows)


def sort_outflows(outflows: Iterable[Outflow]) -> list[Outflow]:
    return sorted(outflows, key=lambda o: (o.scheduled_date, o.ref))
