"""The Vero Proof fixture: synthetic documents, mailbox and reference values for Pizzeria Amalfi
(docs/PROOF.md §4). It must agree with the bank statement, so it is validated against it on load."""

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from api.domain.importer import StatementImport
from api.money import Cents, DecimalStr, Money

_PERIOD_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")


class ProofDatasetError(ValueError):
    pass


def _validate_period(value: object) -> str:
    if isinstance(value, str) and _PERIOD_RE.fullmatch(value):
        return value
    raise ValueError('must be a month as "YYYY-MM"')


Period = Annotated[str, BeforeValidator(_validate_period)]
Box = tuple[float, float, float, float]


class DocumentType(StrEnum):
    LEASE = "LEASE"
    CONTRACT = "CONTRACT"
    INVOICE = "INVOICE"
    RENT_ADJUSTMENT = "RENT_ADJUSTMENT"
    NOTICE = "NOTICE"
    PRICE_LIST = "PRICE_LIST"
    PAYMENT_PLAN = "PAYMENT_PLAN"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Counterparty(_Model):
    id: str
    name: str
    aliases: tuple[str, ...] = ()


class TextBlock(_Model):
    id: str
    text: str
    text_en: str
    box: Box | None = None


class Page(_Model):
    page: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    blocks: tuple[TextBlock, ...] = ()


class LineItem(_Model):
    id: str
    description: str
    quantity: DecimalStr
    unit: str
    unit_price: DecimalStr
    vat_rate: int = Field(ge=0, le=10_000)
    net: Money
    gross: Money | None = None


class Totals(_Model):
    net: Money
    vat: Money
    gross: Money


class Clause(_Model):
    id: str
    section: str
    title: str
    text: str
    text_en: str
    page: int = Field(ge=1)
    box: Box | None = None
    parameters: dict[str, Any] = {}


class PriceListItem(_Model):
    id: str
    description: str
    unit: str
    unit_price: DecimalStr
    vat_rate: int = Field(ge=0, le=10_000)


class PriceList(_Model):
    valid_from: date
    valid_to: date | None = None
    items: tuple[PriceListItem, ...]

    def valid_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


class RentAdjustment(_Model):
    old_amount: Money
    new_amount: Money
    effective_from: date
    frequency: Literal["MONTHLY"]


class Highlight(_Model):
    """Where a value is printed: `ref` is a path such as `lines.l1.unit_price`, `totals.gross`,
    `rent_adjustment.new_amount` or `price_list.p1` (the item's price). Text blocks and clauses
    carry their own box."""

    ref: str
    page: int = Field(ge=1)
    text: str
    box: Box


class Document(_Model):
    id: str
    type: DocumentType
    counterparty_id: str
    title: str
    title_en: str
    date: date
    language: Literal["de", "en", "it"]
    currency: Literal["EUR", "CHF"]
    document_number: str | None = None
    cost_ref: str | None = None
    source: Literal["DOCUMENTS", "MAILBOX", "UPLOAD"]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pages: tuple[Page, ...] = Field(min_length=1)
    lines: tuple[LineItem, ...] = ()
    totals: Totals | None = None
    clauses: tuple[Clause, ...] = ()
    price_list: PriceList | None = None
    rent_adjustment: RentAdjustment | None = None
    highlights: tuple[Highlight, ...] = ()

    @property
    def preloaded(self) -> bool:
        """In the demo data from the start: the owner's documents and the mailbox's
        attachments. An UPLOAD document arrives only when the owner uploads it."""
        return self.source != "UPLOAD"

    @property
    def listed(self) -> bool:
        """Shown in the document list from the start. A mailbox attachment is found only by
        searching the inbox, and becomes visible when a finding cites it."""
        return self.source == "DOCUMENTS"

    def clause(self, clause_id: str) -> Clause | None:
        return next((c for c in self.clauses if c.id == clause_id), None)


class MailAddress(_Model):
    name: str
    address: str


class Email(_Model):
    id: str
    thread_id: str
    in_reply_to: str | None = None
    date: date
    sender: MailAddress = Field(alias="from")
    to: MailAddress
    subject: str
    body: str
    language: Literal["de", "en", "it"]
    counterparty_id: str | None = None
    attachments: tuple[str, ...] = ()


class ReferenceValue(_Model):
    period: Period
    value: DecimalStr


class ReferenceSeries(_Model):
    id: str
    name: Literal["DESTATIS_VPI", "EUR_CHF"]
    description: str
    unit: Literal["INDEX_TENTHS", "EUR_PER_CHF"]
    demo: bool
    values: tuple[ReferenceValue, ...] = Field(min_length=1)


class ProofDataset(_Model):
    version: Literal[1]
    description: str
    counterparties: tuple[Counterparty, ...]
    documents: tuple[Document, ...]
    mailbox: tuple[Email, ...]
    reference_values: tuple[ReferenceSeries, ...]

    def counterparty(self, counterparty_id: str) -> Counterparty | None:
        return next((c for c in self.counterparties if c.id == counterparty_id), None)

    def document(self, document_id: str) -> Document | None:
        return next((d for d in self.documents if d.id == document_id), None)


def round_half_up(value: Decimal) -> Cents:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def line_net(quantity: Decimal, unit_price: Decimal) -> Cents:
    """Quantity × unit price in euros, as cents rounded half up."""
    return round_half_up(quantity * unit_price * 100)


def vat_per_rate(lines: tuple[LineItem, ...]) -> dict[int, Cents]:
    """VAT rounded per rate on the net totals, as German invoices do."""
    net_by_rate: dict[int, Cents] = defaultdict(int)
    for line in lines:
        net_by_rate[line.vat_rate] += line.net
    return {rate: round_half_up(Decimal(net) * rate / 10_000) for rate, net in net_by_rate.items()}


class LineCheck(StrEnum):
    UNCHANGED = "UNCHANGED"
    PRICE_LIST = "PRICE_LIST"
    UNEXPLAINED = "UNEXPLAINED"


@dataclass(frozen=True, slots=True)
class InvoiceLineCheck:
    """One line of a later invoice compared with the supplier's previous invoice and price list."""

    counterparty_id: str
    document_id: str
    previous_document_id: str
    line_id: str
    description: str
    result: LineCheck
    previous_unit_price: Decimal | None
    unit_price: Decimal
    price_list_id: str | None = None
    price_list_item_id: str | None = None


def compare_invoice(
    previous: Document, current: Document, price_lists: list[Document]
) -> list[InvoiceLineCheck]:
    """The lines of `current` whose unit price is new: different from `previous` (the same
    supplier's invoice before it), or not on it. A price list valid on the invoice date that
    gives exactly that price explains the line; otherwise it is unexplained."""
    before = {line.description: line.unit_price for line in previous.lines}
    checks: list[InvoiceLineCheck] = []
    for line in current.lines:
        previous_price = before.get(line.description)
        if previous_price == line.unit_price:
            continue
        explained = next(
            (
                (pl.id, item.id)
                for pl in price_lists
                if pl.price_list is not None and pl.price_list.valid_on(current.date)
                for item in pl.price_list.items
                if item.description == line.description and item.unit_price == line.unit_price
            ),
            None,
        )
        checks.append(
            InvoiceLineCheck(
                counterparty_id=current.counterparty_id,
                document_id=current.id,
                previous_document_id=previous.id,
                line_id=line.id,
                description=line.description,
                result=LineCheck.PRICE_LIST if explained else LineCheck.UNEXPLAINED,
                previous_unit_price=previous_price,
                unit_price=line.unit_price,
                price_list_id=explained[0] if explained else None,
                price_list_item_id=explained[1] if explained else None,
            )
        )
    return checks


def invoice_line_checks(dataset: ProofDataset) -> list[InvoiceLineCheck]:
    """compare_invoice for each supplier's preloaded invoices in date order."""
    invoices: dict[str, list[Document]] = defaultdict(list)
    price_lists: dict[str, list[Document]] = defaultdict(list)
    for document in dataset.documents:
        if document.type is DocumentType.INVOICE and document.preloaded:
            invoices[document.counterparty_id].append(document)
        elif document.price_list is not None:
            price_lists[document.counterparty_id].append(document)

    checks: list[InvoiceLineCheck] = []
    for supplier, documents in invoices.items():
        ordered = sorted(documents, key=lambda d: (d.date, d.id))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            checks += compare_invoice(previous, current, price_lists[supplier])
    return checks


class _Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, message: str) -> None:
        self.items.append(message)

    def unique(self, kind: str, ids: list[str]) -> None:
        seen: set[str] = set()
        for item_id in ids:
            if item_id in seen:
                self.add(f"duplicate {kind} id {item_id!r}")
            seen.add(item_id)


def _check_box(problems: _Problems, where: str, box: Box | None, page: Page | None) -> None:
    if box is None:
        return
    x0, y0, x1, y1 = box
    if page is None:
        problems.add(f"{where}: box on a page the document doesn't have")
    elif not (0 <= x0 < x1 <= page.width and 0 <= y0 < y1 <= page.height):
        problems.add(f"{where}: box {list(box)} is not inside the page")


def _highlight_targets(document: Document) -> set[str]:
    targets = {"counterparty", "document_number", "date"}
    for line in document.lines:
        targets |= {f"lines.{line.id}.{f}" for f in LineItem.model_fields if f != "id"}
    if document.totals is not None:
        targets |= {f"totals.{f}" for f in Totals.model_fields}
    if document.rent_adjustment is not None:
        targets |= {f"rent_adjustment.{f}" for f in RentAdjustment.model_fields}
    if document.price_list is not None:
        targets |= {f"price_list.{item.id}" for item in document.price_list.items}
    return targets


def _check_document(problems: _Problems, dataset: ProofDataset, document: Document) -> None:
    where = f"document {document.id}"
    if dataset.counterparty(document.counterparty_id) is None:
        problems.add(f"{where}: unknown counterparty {document.counterparty_id!r}")

    pages = {p.page: p for p in document.pages}
    if sorted(pages) != list(range(1, len(document.pages) + 1)):
        problems.add(f"{where}: pages must be numbered 1..{len(document.pages)}")
    problems.unique(f"{where} block", [b.id for p in document.pages for b in p.blocks])
    problems.unique(f"{where} line", [line.id for line in document.lines])
    problems.unique(f"{where} clause", [c.id for c in document.clauses])
    for p in document.pages:
        for b in p.blocks:
            _check_box(problems, f"{where} block {b.id}", b.box, p)
    for c in document.clauses:
        if c.page not in pages:
            problems.add(f"{where} clause {c.id}: page {c.page} does not exist")
        _check_box(problems, f"{where} clause {c.id}", c.box, pages.get(c.page))

    targets = _highlight_targets(document)
    for h in document.highlights:
        if h.ref not in targets:
            problems.add(f"{where}: highlight for unknown {h.ref!r}")
        _check_box(problems, f"{where} highlight {h.ref}", h.box, pages.get(h.page))

    is_invoice = document.type is DocumentType.INVOICE
    if is_invoice and (not document.document_number or not document.lines or not document.totals):
        problems.add(f"{where}: an invoice needs a document number, lines and totals")
    if not is_invoice and (document.lines or document.totals):
        problems.add(f"{where}: only invoices have lines and totals")
    if (document.type is DocumentType.RENT_ADJUSTMENT) != (document.rent_adjustment is not None):
        problems.add(f"{where}: rent adjustment fields belong to RENT_ADJUSTMENT documents only")
    if (document.type is DocumentType.PRICE_LIST) != (document.price_list is not None):
        problems.add(f"{where}: a price list belongs to PRICE_LIST documents only")

    if document.price_list is not None:
        problems.unique(f"{where} price list item", [i.id for i in document.price_list.items])
        problems.unique(
            f"{where} price list description", [i.description for i in document.price_list.items]
        )
        valid_to = document.price_list.valid_to
        if valid_to is not None and valid_to < document.price_list.valid_from:
            problems.add(f"{where}: price list ends before it starts")

    _check_arithmetic(problems, where, document)


def _check_arithmetic(problems: _Problems, where: str, document: Document) -> None:
    for line in document.lines:
        expected = line_net(line.quantity, line.unit_price)
        if line.net != expected:
            problems.add(
                f"{where} line {line.id}: {line.quantity} × {line.unit_price} = {expected} cents, "
                f"not {line.net}"
            )
        if line.gross is not None:
            vat = round_half_up(Decimal(line.net) * line.vat_rate / 10_000)
            if line.gross != line.net + vat:
                problems.add(f"{where} line {line.id}: gross is not net + VAT")
    totals = document.totals
    if totals is None or not document.lines:
        return
    net = sum(line.net for line in document.lines)
    vat = sum(vat_per_rate(document.lines).values())
    if totals.net != net:
        problems.add(f"{where}: the lines add up to {net} cents, the net total is {totals.net}")
    if totals.vat != vat:
        problems.add(f"{where}: VAT per rate gives {vat} cents, the VAT total is {totals.vat}")
    if totals.gross != totals.net + totals.vat:
        problems.add(f"{where}: gross {totals.gross} is not net + VAT")


def _check_against_statement(
    problems: _Problems, dataset: ProofDataset, document: Document, statement: StatementImport
) -> None:
    """An invoice was paid on its date: one statement payment with the same counterparty, date,
    amount and the invoice number in the reference."""
    counterparty = dataset.counterparty(document.counterparty_id)
    if counterparty is None or document.totals is None or not document.document_number:
        return
    payments = [
        t
        for t in statement.transactions
        if t.counterparty == counterparty.name
        and t.booking_date == document.date
        and t.amount == -document.totals.gross
        and document.document_number in t.reference
    ]
    if len(payments) != 1:
        problems.add(
            f"document {document.id}: expected one statement payment of {document.totals.gross} "
            f"cents to {counterparty.name} on {document.date} for {document.document_number}, "
            f"found {len(payments)}"
        )


def _check_mailbox(problems: _Problems, dataset: ProofDataset, as_of: date) -> None:
    emails = {e.id: e for e in dataset.mailbox}
    attached = {a for e in dataset.mailbox for a in e.attachments}
    for document in dataset.documents:
        if document.source == "MAILBOX" and document.id not in attached:
            problems.add(f"document {document.id}: a MAILBOX document must be attached to an email")
    for email in dataset.mailbox:
        where = f"email {email.id}"
        if email.date >= as_of:
            problems.add(f"{where}: dated {email.date}, not before {as_of}")
        known = email.counterparty_id is None or dataset.counterparty(email.counterparty_id)
        if not known:
            problems.add(f"{where}: unknown counterparty {email.counterparty_id!r}")
        for attachment in email.attachments:
            if dataset.document(attachment) is None:
                problems.add(f"{where}: attachment {attachment!r} is not a document")
        if email.in_reply_to is not None:
            parent = emails.get(email.in_reply_to)
            if parent is None:
                problems.add(f"{where}: replies to unknown email {email.in_reply_to!r}")
            elif parent.thread_id != email.thread_id or parent.date > email.date:
                problems.add(f"{where}: replies to {parent.id} from another thread or later")


def _check_reference_values(
    problems: _Problems, dataset: ProofDataset, statement: StatementImport
) -> None:
    for series in dataset.reference_values:
        where = f"reference values {series.id}"
        if not series.demo:
            problems.add(f"{where}: must be flagged as demo values")
        problems.unique(f"{where} period", [v.period for v in series.values])
        if series.unit == "INDEX_TENTHS":
            for value in series.values:
                if value.value != value.value.to_integral_value():
                    problems.add(f"{where} {value.period}: an index is in whole tenths")
        if series.name == "EUR_CHF":
            for value in series.values:
                rates = {
                    Decimal(t.fx_rate)
                    for t in statement.transactions
                    if t.original_currency == "CHF"
                    and t.fx_rate is not None
                    and t.booking_date.strftime("%Y-%m") == value.period
                }
                if rates != {value.value}:
                    problems.add(
                        f"{where} {value.period}: {value.value} does not match the statement "
                        f"rates {sorted(map(str, rates))}"
                    )


def validate_dataset(dataset: ProofDataset, statement: StatementImport, as_of: date) -> None:
    """Raise ProofDatasetError listing everything that disagrees with itself or the statement."""
    problems = _Problems()
    problems.unique("counterparty", [c.id for c in dataset.counterparties])
    problems.unique("document", [d.id for d in dataset.documents])
    problems.unique("email", [e.id for e in dataset.mailbox])
    problems.unique("reference series", [s.id for s in dataset.reference_values])

    for document in dataset.documents:
        if document.date >= as_of:
            problems.add(f"document {document.id}: dated {document.date}, not before {as_of}")
        _check_document(problems, dataset, document)
        if document.type is DocumentType.INVOICE:
            _check_against_statement(problems, dataset, document, statement)

    _check_mailbox(problems, dataset, as_of)
    _check_reference_values(problems, dataset, statement)

    listed = {
        (d.counterparty_id, item.description)
        for d in dataset.documents
        if d.price_list is not None
        for item in d.price_list.items
    }
    for check in invoice_line_checks(dataset):
        known_goods = check.previous_unit_price is not None or (
            (check.counterparty_id, check.description) in listed
        )
        if check.result is LineCheck.UNEXPLAINED and known_goods:
            problems.add(
                f"document {check.document_id} line {check.line_id}: {check.description!r} at "
                f"{check.unit_price} is neither on {check.previous_document_id} nor on a price "
                "list valid on that date"
            )

    if problems.items:
        raise ProofDatasetError("Proof dataset is invalid: " + "; ".join(problems.items))


def parse_dataset(data: object) -> ProofDataset:
    try:
        return ProofDataset.model_validate(data)
    except ValidationError as exc:
        raise ProofDatasetError(f"Proof dataset is invalid: {exc}") from exc


def load_dataset(path: Path, statement: StatementImport, as_of: date) -> ProofDataset:
    """Read, parse and validate the fixture; any problem raises ProofDatasetError."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProofDatasetError(f"cannot read Proof dataset {path}: {exc}") from exc
    dataset = parse_dataset(data)
    validate_dataset(dataset, statement, as_of)
    return dataset
