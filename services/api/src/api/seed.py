"""Demo data for Pizzeria Amalfi that is not in the bank statement."""

from datetime import datetime

from api.domain.models import Category, Company, Mandate, MandateStatus
from api.domain.schedule import ScheduledPayment

TAX_OFFICE = "Tax Office Frankfurt am Main"

PIZZERIA_COMPANY = Company(
    name="Pizzeria Amalfi",
    owner="Vincenzo Amalfi",
    currency="EUR",
    account="DE89370400440532013000",
)


def initial_mandate(created_at: datetime) -> Mandate:
    """Mandate v1: conservative, Vero may not act alone."""
    return Mandate(
        version=1,
        status=MandateStatus.ACTIVE,
        cash_floor=-500_000,
        act_alone_enabled=False,
        max_delay_days=45,
        max_amount_per_action=500_000,
        max_amount_per_day=500_000,
        protected_categories=frozenset({Category.PAYROLL, Category.SOCIAL_SECURITY, Category.TAX}),
        credit_enabled=True,
        max_credit_amount=3_000_000,
        paused=False,
        created_at=created_at,
        activated_at=created_at,
        note="Initial rules: stay within the 5 000 € overdraft, always ask before acting.",
    )


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
