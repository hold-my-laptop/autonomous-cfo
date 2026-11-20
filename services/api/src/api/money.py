"""Money is always an integer number of cents. Never floats."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

type Cents = int

MONEY_PATTERN = r"^-?[0-9]+$"
_MONEY_RE = re.compile(MONEY_PATTERN)
_GERMAN_AMOUNT_RE = re.compile(r"^(-?)(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?$")


def parse_german_amount(text: str) -> Cents:
    """'-4166,67' -> -416667, '1.234,5' -> 123450, '275' -> 27500."""
    match = _GERMAN_AMOUNT_RE.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"not a German amount: {text!r}")
    sign, whole, fraction = match.groups()
    cents = int(whole.replace(".", "")) * 100 + int((fraction or "0").ljust(2, "0"))
    return -cents if sign else cents


def convert(cents: Cents, rate: Decimal | str) -> Cents:
    """Multiply by an exchange rate, rounding half up to the cent."""
    try:
        factor = Decimal(rate)
    except InvalidOperation as exc:
        raise ValueError(f"invalid rate: {rate!r}") from exc
    return int((Decimal(cents) * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_eur(cents: Cents) -> str:
    """-2276082 -> '-22 760,82 €' (display only)."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    grouped = f"{whole:,}".replace(",", " ")
    return f"{sign}{grouped},{fraction:02d} €"


def _validate_money(value: object) -> Cents:
    if isinstance(value, bool):
        raise ValueError("money must be integer cents")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _MONEY_RE.fullmatch(value):
        return int(value)
    raise ValueError('money must be a string of integer cents, e.g. "125050"')


Money = Annotated[
    int,
    BeforeValidator(_validate_money),
    PlainSerializer(str, return_type=str),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": MONEY_PATTERN,
            "description": 'Integer cents as a string: "125050" = 1 250,50 €',
            "examples": ["125050"],
        }
    ),
]
