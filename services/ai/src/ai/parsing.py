"""Deterministic parsing of values as printed on (mostly German) documents.

The model never writes numbers: it points at printed text, and these functions turn
that text into contract values (DATA_CONTRACTS §0):
  money   -> integer cents as a string   "4.104,00 €" -> "410400"
  decimal -> Decimal string (<= 6 dp)    "0,1234 €/kWh" -> "0.1234"
  rate    -> basis points as int         "19 %" -> 1900
  date    -> YYYY-MM-DD                  "01.06.2025" / "1. Juni 2025" -> "2025-06-01"
Every function raises ParseError rather than guessing.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

DECIMAL_RE = re.compile(r"^-?[0-9]{1,9}(\.[0-9]{1,6})?$")  # contract §0 Decimal

MONTHS = {
    # German
    "januar": 1, "jan": 1, "jänner": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8,
    "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10, "november": 11,
    "nov": 11, "dezember": 12, "dez": 12,
    # English
    "january": 1, "february": 2, "march": 3, "mar": 3, "may": 5, "june": 6, "july": 7,
    "october": 10, "oct": 10, "december": 12, "dec": 12,
}
CURRENCIES = {"€": "EUR", "eur": "EUR", "euro": "EUR", "chf": "CHF", "fr.": "CHF", "$": "USD",
              "usd": "USD", "£": "GBP", "gbp": "GBP", "pln": "PLN", "zł": "PLN", "zl": "PLN"}
FREQUENCIES = {"monatlich": "MONTHLY", "monthly": "MONTHLY", "pro monat": "MONTHLY",
               "per month": "MONTHLY", "je monat": "MONTHLY", "mtl": "MONTHLY", "/monat": "MONTHLY"}


class ParseError(ValueError):
    pass


_NUMBER = re.compile(r"\d[\d.,'\u00a0\u202f ]*\d|\d")


def _number(text: str) -> Decimal:
    """The single number in `text`. Units and currency signs around it are ignored; two
    numbers in one value are an error. Separators (German documents first):
      both , and .        -> the last one is the decimal mark  (1.234,56 / 1,234.56)
      only commas         -> one comma = decimal (2,5 / 4,00); several = thousands (1,234,567)
      only dots           -> several = thousands (1.234.567); one dot followed by exactly
                             3 digits = thousands (1.234); otherwise decimal (12.5)
    A minus before the number or right after it (German "123,45-") makes it negative."""
    t = text.replace("\u2212", "-").replace("\u2013", "-")
    found = list(_NUMBER.finditer(t))
    if len(found) != 1:
        raise ParseError(f"expected one number in {text!r}, found {len(found)}")
    m = found[0]
    raw = re.sub(r"[\s'\u00a0\u202f]", "", m.group(0))
    before, after = t[:m.start()].rstrip(), t[m.end():].lstrip()
    neg = before.endswith("-") or after.startswith("-")
    commas, dots = raw.count(","), raw.count(".")
    if commas and dots:
        dec = "," if raw.rfind(",") > raw.rfind(".") else "."
    elif commas:
        dec = "," if commas == 1 else None
    elif dots:
        dec = None if dots > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", raw) else "."
    else:
        dec = None
    thousands = {",": ".", ".": ",", None: ",." if commas else "."}[dec]
    normal = raw
    for sep in thousands:
        normal = normal.replace(sep, "")
    if dec:
        normal = normal.replace(dec, ".")
    if not re.fullmatch(r"\d+(\.\d+)?", normal):
        raise ParseError(f"cannot read the number in {text!r}")
    value = Decimal(normal)
    return -value if neg else value


def money_cents(text: str) -> str:
    value = _number(text)
    if value.as_tuple().exponent < -2:
        raise ParseError(f"amount {text!r} has more than 2 decimals")
    cents = int((value * 100).to_integral_value())
    if abs(cents) >= 10**15:
        raise ParseError(f"amount {text!r} is too large")
    return str(cents)


def decimal_str(text: str) -> str:
    value = _number(text)
    if value.as_tuple().exponent < -6:
        raise ParseError(f"{text!r} has more than 6 decimals")
    s = format(value.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if not DECIMAL_RE.fullmatch(s):
        raise ParseError(f"{text!r} is out of range for a Decimal")
    return s


def rate_bp(text: str) -> int:
    if "%" not in text and not re.search(r"\b(mwst|ust|vat)\b", text, re.I):
        raise ParseError(f"{text!r} does not look like a VAT rate")
    value = _number(text)
    bp = value * 100
    if bp != bp.to_integral_value() or not 0 <= bp <= 10000:
        raise ParseError(f"VAT rate {text!r} is not a whole number of basis points")
    return int(bp)


def iso_date(text: str) -> str:
    t = text.strip().rstrip(".,;")
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        y, mo, d = map(int, m.groups())
    elif m := re.search(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{2}|\d{4})\b", t):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y += 2000 if y < 100 else 0
    elif m := re.search(r"\b(\d{1,2})\.?\s+([A-Za-zÄÖÜäöü]+)\.?\s+(\d{4})\b", t):
        d, name, y = int(m.group(1)), m.group(2).casefold(), int(m.group(3))
        if name not in MONTHS:
            raise ParseError(f"unknown month in {text!r}")
        mo = MONTHS[name]
    elif m := re.search(r"\b([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b", t):
        name, d, y = m.group(1).casefold(), int(m.group(2)), int(m.group(3))
        if name not in MONTHS:
            raise ParseError(f"unknown month in {text!r}")
        mo = MONTHS[name]
    else:
        raise ParseError(f"cannot read a date in {text!r}")
    try:
        return date(y, mo, d).isoformat()
    except ValueError as e:
        raise ParseError(f"invalid date {text!r}") from e


def currency(text: str) -> str:
    t = text.strip().casefold()
    for sign, code in CURRENCIES.items():
        if sign in t:
            return code
    if re.fullmatch(r"[a-z]{3}", t):
        return t.upper()
    raise ParseError(f"unknown currency {text!r}")


def frequency(text: str) -> str:
    t = text.casefold()
    for word, code in FREQUENCIES.items():
        if word in t:
            return code
    raise ParseError(f"unknown frequency {text!r}")


def round_half_up_cents(value: Decimal) -> int:
    """Euros (Decimal) -> integer cents, rounded half up (the contract's line rounding)."""
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
