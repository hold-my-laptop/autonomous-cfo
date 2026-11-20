"""Demo data for Pizzeria Amalfi that is not in the bank statement."""

from api.domain.models import Category
from api.domain.schedule import ScheduledPayment

TAX_OFFICE = "Tax Office Frankfurt am Main"

PIZZERIA_TAX_SCHEDULE = (
    ScheduledPayment(
        ref_prefix="ITAX",
        counterparty=TAX_OFFICE,
        description="Income tax prepayment",
        category=Category.TAX,
        amount=115_000,
        day_of_month=10,
        months=frozenset({3, 6, 9, 12}),
    ),
    ScheduledPayment(
        ref_prefix="TTAX",
        counterparty=TAX_OFFICE,
        description="Trade tax prepayment",
        category=Category.TAX,
        amount=22_500,
        day_of_month=15,
        months=frozenset({2, 5, 8, 11}),
    ),
)
