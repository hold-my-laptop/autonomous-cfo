"""Money is always an integer number of cents. Never floats."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

type Cents = int

MAX_MONEY_DIGITS = 15
MONEY_PATTERN = rf"^-?[0-9]{{1,{MAX_MONEY_DIGITS}}}$"
_MONEY_LIMIT = 10**MAX_MONEY_DIGITS
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
    if isinstance(value, int) and abs(value) < _MONEY_LIMIT:
        return value
    if isinstance(value, str) and _MONEY_RE.fullmatch(value):
        return int(value)
    raise ValueError(
        f"money must be a string of at most {MAX_MONEY_DIGITS} digits of integer cents, "
        'e.g. "125050"'
    )


Money = Annotated[
    int,
    BeforeValidator(_validate_money),
    PlainSerializer(str, return_type=str),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": MONEY_PATTERN,
            "description": 'Integer cents as a string: "125050" = 1 250,50 €, '
            f"at most {MAX_MONEY_DIGITS} digits",
            "examples": ["125050"],
        }
    ),
]


DECIMAL_PATTERN = r"^-?[0-9]{1,9}(\.[0-9]{1,6})?$"
_DECIMAL_RE = re.compile(DECIMAL_PATTERN)


def _validate_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal) and _DECIMAL_RE.fullmatch(format(value, "f")):
        return value
    if isinstance(value, str) and _DECIMAL_RE.fullmatch(value):
        return Decimal(value)
    raise ValueError('must be a decimal string such as "12.5", never a number')


DecimalStr = Annotated[
    Decimal,
    BeforeValidator(_validate_decimal),
    PlainSerializer(lambda d: format(d, "f"), return_type=str),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": DECIMAL_PATTERN,
            "description": "A decimal number as a string, never a float: unit prices and "
            'quantities, e.g. "0.1234" or "2.5"',
            "examples": ["9.40"],
        }
    ),
]
