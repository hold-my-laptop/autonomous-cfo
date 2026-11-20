from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta

from api.domain.models import Transaction
from api.money import Cents


def balance_before(opening: Cents, transactions: Iterable[Transaction], day: date) -> Cents:
    """Balance at the start of `day`: opening + every transaction booked before it."""
    return opening + sum(t.amount for t in transactions if t.booking_date < day)


def daily_closing_balances(
    opening: Cents, transactions: Iterable[Transaction], start: date, end: date
) -> dict[date, Cents]:
    """End-of-day balances from `start` to `end`.

    `opening` is the balance at the start of `start`."""
    net_by_day: dict[date, Cents] = defaultdict(int)
    for transaction in transactions:
        if start <= transaction.booking_date <= end:
            net_by_day[transaction.booking_date] += transaction.amount

    balances: dict[date, Cents] = {}
    balance, day = opening, start
    while day <= end:
        balance += net_by_day[day]
        balances[day] = balance
        day += timedelta(days=1)
    return balances
