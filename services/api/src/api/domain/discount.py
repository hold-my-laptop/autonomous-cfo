"""Early-payment discount vs. bank credit: which way of getting cash early is cheaper?

If a customer pays an invoice `days_early` days before its due date, the business gives
a discount but avoids borrowing that money for those days. Offering the discount makes
sense only while it costs less than the financing it avoids:

    financing_cost = amount * annual_rate * days_early / 365  (+ fees)
    break_even     = financing_cost / amount                  (as a rate)

Simple interest, actual/365, half-up rounding to the cent. Deterministic code, never
the LLM. Rates are in basis points (bp): 1200 bp = 12 % a year, 150 bp = 1.5 %.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from api.money import Cents

ENGINE_VERSION = "discount-1.0.0"
DAY_COUNT = 365
DISCOUNT_STEP_BP = 25  # offers are 0.25 % steps


class CheaperOption(StrEnum):
    DISCOUNT = "DISCOUNT"
    CREDIT = "CREDIT"
    EQUAL = "EQUAL"


@dataclass(frozen=True, slots=True)
class DiscountComparison:
    amount: Cents
    days_early: int
    annual_rate_bp: int
    discount_bp: int
    discount_cost: Cents
    financing_cost: Cents
    saving: Cents  # financing_cost - discount_cost; positive means the discount is cheaper
    break_even_bp: int  # largest discount (whole bp) that is not more expensive than borrowing
    cheaper: CheaperOption
    engine_version: str = ENGINE_VERSION


def _cents(value: Decimal) -> Cents:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def financing_cost(amount: Cents, days: int, annual_rate_bp: int, fees: Cents = 0) -> Cents:
    """Interest for borrowing `amount` for `days` at `annual_rate_bp`, plus fixed fees."""
    if amount < 0 or days < 0 or annual_rate_bp < 0 or fees < 0:
        raise ValueError("amount, days, rate and fees must not be negative")
    interest = Decimal(amount) * annual_rate_bp * days / (10_000 * DAY_COUNT)
    return _cents(interest) + fees


def compare(
    amount: Cents, days_early: int, annual_rate_bp: int, discount_bp: int, fees: Cents = 0
) -> DiscountComparison:
    if amount <= 0:
        raise ValueError("amount must be positive")
    if not 0 <= discount_bp <= 10_000:
        raise ValueError("discount_bp must be between 0 and 10000")
    financing = financing_cost(amount, days_early, annual_rate_bp, fees)
    discount = _cents(Decimal(amount) * discount_bp / 10_000)
    saving = financing - discount
    break_even = int(Decimal(financing) * 10_000 / Decimal(amount))  # floor
    cheaper = CheaperOption.DISCOUNT if saving > 0 else CheaperOption.CREDIT if saving < 0 else CheaperOption.EQUAL
    return DiscountComparison(
        amount=amount,
        days_early=days_early,
        annual_rate_bp=annual_rate_bp,
        discount_bp=discount_bp,
        discount_cost=discount,
        financing_cost=financing,
        saving=saving,
        break_even_bp=break_even,
        cheaper=cheaper,
    )


def suggest_discount_bp(amount: Cents, days_early: int, annual_rate_bp: int, fees: Cents = 0) -> int | None:
    """Largest offer in 0.25 % steps that is still strictly cheaper than borrowing.
    None when even the smallest step is not worth it."""
    be = compare(amount, days_early, annual_rate_bp, 0, fees).break_even_bp
    step = DISCOUNT_STEP_BP
    best = (be // step) * step
    while best > 0 and compare(amount, days_early, annual_rate_bp, best, fees).saving <= 0:
        best -= step
    return best if best > 0 else None
