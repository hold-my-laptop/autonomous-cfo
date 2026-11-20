"""Business dates are calendar days (the bank books on weekends too)."""

from collections.abc import Iterator
from datetime import date, timedelta


def parse_german_date(text: str) -> date:
    """'31.12.25' -> 2025-12-31, '05.05.2025' -> 2025-05-05."""
    try:
        day, month, year = (int(part) for part in text.strip().split("."))
    except ValueError as exc:
        raise ValueError(f"not a dd.mm.yy date: {text!r}") from exc
    if year < 100:
        year += 2000
    return date(year, month, day)


def days(start: date, count: int) -> Iterator[date]:
    """`count` consecutive calendar days starting at `start`."""
    for offset in range(count):
        yield start + timedelta(days=offset)


def months_between(start: date, end: date) -> Iterator[tuple[int, int]]:
    """(year, month) for every calendar month touched by start..end."""
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def window_end(start: date, count: int) -> date:
    """Last day of a window of `count` days starting at `start`."""
    return start + timedelta(days=count - 1)
