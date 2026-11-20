"""Detect monthly recurring payments in the statement history and project them forward."""

import calendar
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID, uuid5

from api.dates import months_between
from api.domain.models import Category, Outflow, OutflowSource, RecurringPayment, Transaction
from api.money import Cents, convert

FIXED_COST_CATEGORIES = frozenset(
    {
        Category.RENT,
        Category.PAYROLL,
        Category.SOCIAL_SECURITY,
        Category.TAX,
        Category.UTILITIES,
        Category.PARTNER_TRANSFER,
    }
)
MIN_CONSECUTIVE_MONTHS = 3
AMOUNT_TOLERANCE = 0.02
DAY_TOLERANCE = 1

_PREFIXES = {
    Category.RENT: "RENT",
    Category.PAYROLL: "PAY",
    Category.SOCIAL_SECURITY: "SOC",
    Category.TAX: "WTAX",
    Category.UTILITIES: "UTIL",
    Category.PARTNER_TRANSFER: "XFER",
}
_ID_NAMESPACE = UUID("2d7a9c41-5e3b-4f8a-9b6c-1e0d2f3a4b5c")


@dataclass(frozen=True, slots=True)
class _Occurrence:
    day: date
    amount: Cents
    compare_amount: Cents
    counterparties: frozenset[str]
    original_currency: str | None
    fx_rate: str | None


def _month_index(day: date) -> int:
    return day.year * 12 + day.month


def _group_key(transaction: Transaction) -> tuple[str, ...]:
    if transaction.category is Category.PAYROLL:
        return (Category.PAYROLL,)
    return (transaction.category, transaction.counterparty, transaction.type)


def _occurrences(transactions: list[Transaction]) -> list[_Occurrence]:
    by_day: dict[date, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        by_day[transaction.booking_date].append(transaction)
    result = []
    for day in sorted(by_day):
        items = by_day[day]
        amount = -sum(t.amount for t in items)
        foreign = [t for t in items if t.original_amount is not None]
        result.append(
            _Occurrence(
                day=day,
                amount=amount,
                compare_amount=-sum(t.original_amount for t in foreign) if foreign else amount,
                counterparties=frozenset(t.counterparty for t in items),
                original_currency=foreign[-1].original_currency if foreign else None,
                fx_rate=foreign[-1].fx_rate if foreign else None,
            )
        )
    return result


def _latest_monthly_run(occurrences: list[_Occurrence]) -> list[_Occurrence] | None:
    """The most recent run of consecutive months with exactly one occurrence each."""
    months = [_month_index(o.day) for o in occurrences]
    if len(set(months)) != len(months):
        return None
    run = [occurrences[-1]]
    for occurrence in reversed(occurrences[:-1]):
        if _month_index(run[0].day) - _month_index(occurrence.day) != 1:
            break
        run.insert(0, occurrence)
    return run


def _is_stable(run: list[_Occurrence]) -> bool:
    last = run[-1]
    for occurrence in run:
        if abs(occurrence.compare_amount - last.compare_amount) > AMOUNT_TOLERANCE * abs(
            last.compare_amount
        ):
            return False
        if abs(occurrence.day.day - last.day.day) > DAY_TOLERANCE:
            return False
    return True


def detect_recurring(transactions: Iterable[Transaction], as_of: date) -> list[RecurringPayment]:
    """Monthly payments seen in at least 3 consecutive months up to the month before `as_of`."""
    groups: dict[tuple[str, ...], list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        if (
            transaction.booking_date < as_of
            and transaction.amount < 0
            and transaction.category in FIXED_COST_CATEGORIES
        ):
            groups[_group_key(transaction)].append(transaction)

    detected: list[RecurringPayment] = []
    used_prefixes: dict[str, int] = defaultdict(int)
    for key, items in sorted(groups.items(), key=lambda item: str(item[0])):
        run = _latest_monthly_run(_occurrences(items))
        if (
            run is None
            or len(run) < MIN_CONSECUTIVE_MONTHS
            or _month_index(as_of) - _month_index(run[-1].day) > 1
            or not _is_stable(run)
        ):
            continue

        last = run[-1]
        category = items[0].category
        if last.original_currency is not None and last.fx_rate is not None:
            amount = convert(last.compare_amount, last.fx_rate)
            prefix = last.original_currency
        else:
            amount = last.amount
            prefix = _PREFIXES[category]
        used_prefixes[prefix] += 1
        if used_prefixes[prefix] > 1:
            prefix = f"{prefix}{used_prefixes[prefix]}"

        if category is Category.PAYROLL:
            counterparty = f"Payroll ({len(last.counterparties)} people)"
        else:
            counterparty = items[0].counterparty

        detected.append(
            RecurringPayment(
                id=uuid5(_ID_NAMESPACE, "|".join(key)),
                ref_prefix=prefix,
                counterparty=counterparty,
                category=category,
                day_of_month=last.day.day,
                amount=amount,
                last_seen=last.day,
                occurrences=len(run),
                original_amount=last.compare_amount if last.original_currency else None,
                original_currency=last.original_currency,
                fx_rate=last.fx_rate,
            )
        )
    return detected


def project_outflows(
    recurring: Iterable[RecurringPayment],
    start: date,
    end: date,
    movable_days: Mapping[Category, int] | None = None,
) -> list[Outflow]:
    """Future occurrences between `start` and `end` (inclusive), sorted by date then ref.

    `movable_days` gives, per category, how many days a payment may be postponed;
    categories not listed are not movable."""
    movable_days = movable_days or {}
    outflows = []
    for payment in recurring:
        for year, month in months_between(start, end):
            day = min(payment.day_of_month, calendar.monthrange(year, month)[1])
            due = date(year, month, day)
            if not start <= due <= end:
                continue
            delay = movable_days.get(payment.category)
            ref = f"{payment.ref_prefix}-{month:02d}"
            outflows.append(
                Outflow(
                    id=uuid5(_ID_NAMESPACE, f"{payment.id}|{due.isoformat()}"),
                    ref=ref,
                    counterparty=payment.counterparty,
                    category=payment.category,
                    amount=payment.amount,
                    due_date=due,
                    scheduled_date=due,
                    latest_allowed_date=due + timedelta(days=delay) if delay else None,
                    source=OutflowSource.DETECTED,
                    recurring_id=payment.id,
                    original_amount=payment.original_amount,
                    original_currency=payment.original_currency,
                    fx_rate=payment.fx_rate,
                )
            )
    return sorted(outflows, key=lambda o: (o.due_date, o.ref))
