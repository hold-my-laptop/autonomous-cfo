"""The backend's check of an extraction from the AI service (docs/PROOF.md §5.3).

The model points at printed text; the AI service parses it. The backend parses every value's
printed text again, recomputes the arithmetic, checks the counterparty match and the date, and
only then turns the values into a Document. Its checks are the ones that decide."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.dates import parse_german_date
from api.domain.proof.cost_memory import CostItem, CostKind
from api.domain.proof.dataset import (
    Counterparty,
    Document,
    ProofDataset,
    line_net,
    round_half_up,
)
from api.money import parse_german_amount

_TITLES = {
    "INVOICE": ("Rechnung", "Invoice"),
    "RENT_ADJUSTMENT": ("Mietanpassung", "Rent adjustment"),
    "NOTICE": ("Mitteilung", "Notice"),
    "PRICE_LIST": ("Preisliste", "Price list"),
    "CONTRACT": ("Vertrag", "Contract"),
}
_FREQUENCIES = {
    "monatlich": "MONTHLY",
    "monthly": "MONTHLY",
    "pro monat": "MONTHLY",
    "per month": "MONTHLY",
    "je monat": "MONTHLY",
    "mtl": "MONTHLY",
    "mtl.": "MONTHLY",
}
_CURRENCIES = {"€": "EUR", "eur": "EUR", "euro": "EUR", "chf": "CHF", "fr.": "CHF"}


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ExtractSource(_Loose):
    page: int
    text: str
    box: list[float]


class ExtractValue(_Loose):
    value: Any = None
    source: ExtractSource | None = None


class ExtractCounterparty(ExtractValue):
    matched_id: str | None = None


class ExtractPeriod(_Loose):
    from_: ExtractValue | None = Field(default=None, alias="from")
    to: ExtractValue | None = None


class ExtractLine(_Loose):
    description: ExtractValue | None = None
    quantity: ExtractValue | None = None
    unit: ExtractValue | None = None
    unit_price: ExtractValue | None = None
    vat_rate: ExtractValue | None = None
    net: ExtractValue | None = None
    gross: ExtractValue | None = None


class ExtractTotals(_Loose):
    net: ExtractValue | None = None
    vat: ExtractValue | None = None
    gross: ExtractValue | None = None


class ExtractPage(_Loose):
    page: int
    width: float
    height: float


class ExtractionResult(_Loose):
    """POST ai/v1/documents/extract's answer (DATA_CONTRACTS §3)."""

    document_id: str
    status: str
    errors: list[str] = []
    pages: list[ExtractPage] = []
    doc_type: ExtractValue | None = None
    counterparty: ExtractCounterparty | None = None
    document_number: ExtractValue | None = None
    date: ExtractValue | None = None
    period: ExtractPeriod | None = None
    effective_from: ExtractValue | None = None
    currency: ExtractValue | None = None
    old_amount: ExtractValue | None = None
    new_amount: ExtractValue | None = None
    frequency: ExtractValue | None = None
    lines: list[ExtractLine] = []
    totals: ExtractTotals | None = None
    model: str | None = None


@dataclass
class Checked:
    """A validated extraction: the document when every check passed, else the problems."""

    document: Document | None = None
    matched: bool = False
    printed_counterparty: str | None = None
    problems: list[str] = field(default_factory=list)


def _number(text: str) -> Decimal:
    """The one number in printed text, German separators first: '1.234,56 €' -> 1234.56."""
    found = re.findall(r"-?\d[\d.,\s  ]*\d|-?\d", text)
    if len(found) != 1:
        raise ValueError(f"expected one number in {text!r}")
    raw = re.sub(r"[\s  ]", "", found[0])
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"cannot read the number in {text!r}") from exc


def printed_money(text: str) -> int:
    match = re.search(r"-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|-?\d+(?:,\d{1,2})?", text)
    if match is None:
        raise ValueError(f"no amount in {text!r}")
    return parse_german_amount(match.group(0))


def printed_decimal(text: str) -> Decimal:
    return _number(text)


def printed_rate(text: str) -> int:
    if "%" not in text:
        raise ValueError(f"{text!r} is not a percentage")
    bps = _number(text) * 100
    if bps != bps.to_integral_value():
        raise ValueError(f"{text!r} is not whole basis points")
    return int(bps)


def printed_date(text: str) -> date:
    match = re.search(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b", text)
    if match is None:
        raise ValueError(f"no date in {text!r}")
    return parse_german_date(match.group(0))


def printed_frequency(text: str) -> str:
    key = text.strip().casefold()
    if key not in _FREQUENCIES:
        raise ValueError(f"{text!r} is not a known frequency")
    return _FREQUENCIES[key]


def _normal(text: str) -> str:
    """Case, accents, punctuation and spacing ignored: for matching names."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def supports(printed: str, counterparty: Counterparty) -> bool:
    """The printed name contains the counterparty's name or one of its aliases."""
    text = f" {_normal(printed)} "
    return any(f" {_normal(n)} " in text for n in (counterparty.name, *counterparty.aliases))


class _Reader:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def value(self, path: str, item: ExtractValue | None, parse, *, required: bool = True):
        """The value, if its printed text parses to it; else a problem."""
        if item is None or item.value is None:
            if required:
                self.problems.append(f"{path}: missing")
            return None
        if item.source is None:
            self.problems.append(f"{path}: no printed text")
            return None
        try:
            printed = parse(item.source.text)
            claimed = parse_claimed(parse, item.value)
        except ValueError as exc:
            self.problems.append(f"{path}: {exc}")
            return None
        if printed != claimed:
            self.problems.append(
                f"{path}: printed {item.source.text!r} reads as {printed}, not {item.value!r}"
            )
            return None
        return printed


def parse_claimed(parse, value: Any) -> Any:
    """The AI service's value in the backend's type, to compare with the printed text."""
    if parse is printed_money:
        return int(value)
    if parse is printed_decimal:
        return Decimal(str(value))
    if parse is printed_rate:
        return int(value)
    if parse is printed_date:
        return date.fromisoformat(str(value))
    return str(value).strip()


def _text(text: str) -> str:
    return text.strip()


def _highlight(ref: str, item: ExtractValue | None) -> dict | None:
    if item is None or item.source is None or len(item.source.box) != 4:
        return None
    return {"ref": ref, "page": item.source.page, "text": item.source.text, "box": item.source.box}


def _cost_ref(doc_type: str, counterparty_id: str, cost_memory: tuple[CostItem, ...]) -> str | None:
    wanted = CostKind.FIXED if doc_type == "RENT_ADJUSTMENT" else CostKind.SUPPLIER
    item = next(
        (c for c in cost_memory if c.counterparty_id == counterparty_id and c.kind is wanted),
        None,
    )
    return item.cost_ref if item else None


def check_extraction(
    raw: Any,
    document_id: str,
    sha256: str,
    dataset: ProofDataset,
    cost_memory: tuple[CostItem, ...],
    as_of: date,
) -> Checked:
    """Validate the AI service's answer and build the Document from it."""
    try:
        result = ExtractionResult.model_validate(raw)
    except ValidationError as exc:
        return Checked(problems=[f"unusable answer: {exc.error_count()} shape errors"])
    if result.status != "OK":
        return Checked(problems=[f"the model could not read it: {'; '.join(result.errors)}"])

    reader = _Reader()
    doc_type = result.doc_type.value if result.doc_type else None
    if doc_type not in _TITLES:
        return Checked(problems=[f"doc_type: {doc_type!r} is not a known document type"])

    printed_name = None
    counterparty: Counterparty | None = None
    if result.counterparty is None or result.counterparty.source is None:
        reader.problems.append("counterparty: no printed text")
    else:
        printed_name = result.counterparty.source.text.strip()
        matched_id = result.counterparty.matched_id
        if matched_id is not None:
            counterparty = dataset.counterparty(matched_id)
            if counterparty is None or not supports(printed_name, counterparty):
                reader.problems.append(
                    f"counterparty: the printed name {printed_name!r} does not support "
                    f"{matched_id!r}"
                )
                counterparty = None

    day = reader.value("date", result.date, printed_date)
    if day is not None and day > as_of:
        reader.problems.append(f"date: {day} is after today ({as_of})")
    number = reader.value(
        "document_number",
        result.document_number,
        _text,
        required=doc_type == "INVOICE",
    )
    currency = "EUR"
    if result.currency is not None and result.currency.value:
        currency = str(result.currency.value)
        if currency not in ("EUR", "CHF"):
            reader.problems.append(f"currency: {currency} is not supported")

    lines, net_by_rate = [], defaultdict(int)
    for index, line in enumerate(result.lines, start=1):
        path = f"lines[{index}]"
        description = reader.value(f"{path}.description", line.description, _text)
        quantity = reader.value(f"{path}.quantity", line.quantity, printed_decimal)
        unit = reader.value(f"{path}.unit", line.unit, _text, required=False)
        unit_price = reader.value(f"{path}.unit_price", line.unit_price, printed_decimal)
        rate = reader.value(f"{path}.vat_rate", line.vat_rate, printed_rate)
        net = reader.value(f"{path}.net", line.net, printed_money)
        gross = reader.value(f"{path}.gross", line.gross, printed_money, required=False)
        if None in (description, quantity, unit_price, rate, net):
            continue
        expected = line_net(quantity, unit_price)
        if expected != net:
            reader.problems.append(
                f"{path}: {quantity} × {unit_price} = {expected} cents, but net is {net}"
            )
        net_by_rate[rate] += net
        lines.append(
            {
                "id": f"l{index}",
                "description": description,
                "quantity": format(quantity, "f"),
                "unit": unit or "",
                "unit_price": format(unit_price, "f"),
                "vat_rate": rate,
                "net": str(net),
                "gross": str(gross) if gross is not None else None,
            }
        )

    totals = None
    if result.totals is not None and (result.lines or doc_type == "INVOICE"):
        net = reader.value("totals.net", result.totals.net, printed_money)
        vat = reader.value("totals.vat", result.totals.vat, printed_money)
        gross = reader.value("totals.gross", result.totals.gross, printed_money)
        if None not in (net, vat, gross):
            if net != sum(net_by_rate.values()):
                reader.problems.append(
                    f"totals.net: the lines add up to {sum(net_by_rate.values())}, not {net}"
                )
            expected_vat = sum(
                round_half_up(Decimal(n) * r / 10_000) for r, n in net_by_rate.items()
            )
            if abs(vat - expected_vat) > max(1, len(net_by_rate)):
                reader.problems.append(
                    f"totals.vat: VAT per rate gives {expected_vat} cents, not {vat}"
                )
            if gross != net + vat:
                reader.problems.append(f"totals.gross: {net} + {vat} is not {gross}")
            totals = {"net": str(net), "vat": str(vat), "gross": str(gross)}
    elif doc_type == "INVOICE":
        reader.problems.append("totals: missing")

    rent = None
    if doc_type == "RENT_ADJUSTMENT":
        new_amount = reader.value("new_amount", result.new_amount, printed_money)
        old_amount = reader.value("old_amount", result.old_amount, printed_money, required=False)
        effective = reader.value("effective_from", result.effective_from, printed_date)
        frequency = reader.value("frequency", result.frequency, printed_frequency, required=False)
        if None not in (new_amount, effective):
            rent = {
                "old_amount": str(old_amount) if old_amount is not None else None,
                "new_amount": str(new_amount),
                "effective_from": effective.isoformat(),
                "frequency": frequency or "MONTHLY",
            }

    if reader.problems or day is None:
        return Checked(printed_counterparty=printed_name, problems=reader.problems)
    if counterparty is None:
        return Checked(printed_counterparty=printed_name)

    refs: list[tuple[str, ExtractValue | None]] = [
        ("counterparty", result.counterparty),
        ("date", result.date),
        ("document_number", result.document_number),
    ]
    for index, line in enumerate(result.lines, start=1):
        for name in ("description", "quantity", "unit", "unit_price", "vat_rate", "net"):
            refs.append((f"lines.l{index}.{name}", getattr(line, name)))
    if result.totals is not None and totals is not None:
        refs += [(f"totals.{n}", getattr(result.totals, n)) for n in ("net", "vat", "gross")]
    if rent is not None:
        refs += [
            (f"rent_adjustment.{n}", getattr(result, n))
            for n in ("old_amount", "new_amount", "effective_from", "frequency")
        ]
    pages = result.pages or [ExtractPage(page=1, width=595.28, height=841.89)]
    title, title_en = _TITLES[doc_type]
    try:
        document = Document.model_validate(
            {
                "id": document_id,
                "type": doc_type,
                "counterparty_id": counterparty.id,
                "title": title,
                "title_en": title_en,
                "date": day.isoformat(),
                "language": "de",
                "currency": currency,
                "document_number": number,
                "cost_ref": _cost_ref(doc_type, counterparty.id, cost_memory),
                "source": "UPLOAD",
                "sha256": sha256,
                "pages": [
                    {"page": p.page, "width": p.width, "height": p.height, "blocks": []}
                    for p in pages
                ],
                "lines": lines,
                "totals": totals,
                "rent_adjustment": rent,
                "highlights": [h for ref, item in refs if (h := _highlight(ref, item))],
            }
        )
    except ValidationError as exc:
        return Checked(
            printed_counterparty=printed_name,
            problems=[f"values out of range: {e['msg']}" for e in exc.errors()][:5],
        )
    return Checked(document=document, matched=True, printed_counterparty=printed_name)
