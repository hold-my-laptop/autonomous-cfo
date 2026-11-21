"""Cost Memory: what Vero expects each recurring cost to be (docs/PROOF.md §3).

Fixed costs come from Phase 1's recurring detection on the statement rows before `as_of`, with
the amount the contract behind them sets when there is one. Supplier costs carry the prices of
the price list valid on `as_of`. Cost Memory is base data: rebuilt from the statement and the
fixture on every load and reset, never stored."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from api.domain.models import Category, RecurringPayment, Transaction
from api.domain.proof.dataset import Document, DocumentType, ProofDataset
from api.domain.recurring import AMOUNT_TOLERANCE
from api.money import Cents

COST_REFS = {
    Category.RENT: "RENT",
    Category.UTILITIES: "UTIL",
    Category.PARTNER_TRANSFER: "XFER",
    Category.PAYROLL: "PAY",
    Category.SOCIAL_SECURITY: "SOC",
    Category.TAX: "WTAX",
}
_CONTRACT_TYPES = (DocumentType.LEASE, DocumentType.CONTRACT, DocumentType.PAYMENT_PLAN)


class CostKind(StrEnum):
    FIXED = "FIXED"
    SUPPLIER = "SUPPLIER"


class SourceKind(StrEnum):
    STATEMENT = "STATEMENT"
    CONTRACT = "CONTRACT"
    PRICE_LIST = "PRICE_LIST"
    INVOICE = "INVOICE"


@dataclass(frozen=True, slots=True)
class CostSource:
    """One piece of evidence behind a cost: statement payments, a contract clause, a price
    list or an invoice. `amount` and `currency` are what that evidence says."""

    kind: SourceKind
    date: date
    document_id: str | None = None
    clause_id: str | None = None
    transaction_ids: tuple[UUID, ...] = ()
    amount: Cents | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class ExpectedPrice:
    description: str
    unit: str
    unit_price: Decimal
    vat_rate: int
    valid_from: date
    valid_to: date | None
    document_id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class CostItem:
    cost_ref: str
    kind: CostKind
    counterparty: str
    counterparty_id: str | None
    category: Category
    currency: str
    sources: tuple[CostSource, ...]
    expected_amount: Cents | None = None
    frequency: str | None = None
    due_day: int | None = None
    eur_amount: Cents | None = None
    fx_rate: str | None = None
    recurring_id: UUID | None = None
    outflow_ref_prefix: str | None = None
    prices: tuple[ExpectedPrice, ...] = ()


def _month(day: date) -> int:
    return day.year * 12 + day.month


def _statement_payments(payment: RecurringPayment, history: list[Transaction]) -> list[Transaction]:
    """The statement rows behind a detected payment, in the months it was seen: for payroll
    (one payment to several people) every salary; otherwise the same counterparty's payments
    within Phase 1's amount tolerance, which leaves out e.g. the tax office's quarterly
    prepayments next to the monthly wage tax."""
    first = _month(payment.last_seen) - payment.occurrences + 1
    expected = payment.original_amount or payment.amount

    def matches(t: Transaction) -> bool:
        if payment.category is Category.PAYROLL:
            return True
        paid = -(t.original_amount if payment.original_currency else t.amount)
        return (
            t.counterparty == payment.counterparty
            and abs(paid - expected) <= AMOUNT_TOLERANCE * expected
        )

    return [
        t
        for t in history
        if t.amount < 0
        and t.category is payment.category
        and first <= _month(t.booking_date) <= _month(payment.last_seen)
        and matches(t)
    ]


def _payment_clause(documents: list[Document]) -> tuple[Document, str, dict] | None:
    """The clause that sets the recurring amount: its parameters name an amount, a currency
    and a frequency."""
    for document in sorted(documents, key=lambda d: d.date, reverse=True):
        for clause in document.clauses:
            if {"amount", "currency", "frequency"} <= clause.parameters.keys():
                return document, clause.id, clause.parameters
    return None


def _fixed_item(
    payment: RecurringPayment, history: list[Transaction], dataset: ProofDataset
) -> CostItem:
    cost_ref = COST_REFS[payment.category]
    counterparty = next((c for c in dataset.counterparties if c.name == payment.counterparty), None)
    rows = _statement_payments(payment, history)
    statement_currency = payment.original_currency or "EUR"
    statement_amount = payment.original_amount or payment.amount
    sources = [
        CostSource(
            kind=SourceKind.STATEMENT,
            date=payment.last_seen,
            transaction_ids=tuple(t.id for t in rows),
            amount=statement_amount,
            currency=statement_currency,
        )
    ]
    expected, currency, due_day = statement_amount, statement_currency, payment.day_of_month
    contracts = [
        d
        for d in dataset.documents
        if d.preloaded
        and d.cost_ref == cost_ref
        and d.type in _CONTRACT_TYPES
        and counterparty is not None
        and d.counterparty_id == counterparty.id
    ]
    found = _payment_clause(contracts)
    if found is not None:
        document, clause_id, parameters = found
        expected, currency = int(parameters["amount"]), parameters["currency"]
        due_day = parameters.get("due_day", due_day)
        sources.append(
            CostSource(
                kind=SourceKind.CONTRACT,
                date=document.date,
                document_id=document.id,
                clause_id=clause_id,
                amount=expected,
                currency=currency,
            )
        )
    return CostItem(
        cost_ref=cost_ref,
        kind=CostKind.FIXED,
        counterparty=payment.counterparty,
        counterparty_id=counterparty.id if counterparty else None,
        category=payment.category,
        currency=currency,
        sources=tuple(sorted(sources, key=lambda s: s.date)),
        expected_amount=expected,
        frequency="MONTHLY",
        due_day=due_day,
        eur_amount=payment.amount if payment.original_currency else None,
        fx_rate=payment.fx_rate,
        recurring_id=payment.id,
        outflow_ref_prefix=payment.ref_prefix,
    )


def _supplier_item(
    cost_ref: str, documents: list[Document], dataset: ProofDataset, as_of: date
) -> CostItem:
    counterparty = dataset.counterparty(documents[0].counterparty_id)
    if counterparty is None:
        raise ValueError(f"unknown counterparty {documents[0].counterparty_id!r}")
    sources: list[CostSource] = []
    prices: dict[str, ExpectedPrice] = {}
    for document in sorted(documents, key=lambda d: (d.date, d.id)):
        price_list = document.price_list
        if price_list is not None:
            sources.append(CostSource(SourceKind.PRICE_LIST, document.date, document.id))
            if price_list.valid_on(as_of):
                for item in price_list.items:
                    current = prices.get(item.description)
                    if current is None or current.valid_from <= price_list.valid_from:
                        prices[item.description] = ExpectedPrice(
                            description=item.description,
                            unit=item.unit,
                            unit_price=item.unit_price,
                            vat_rate=item.vat_rate,
                            valid_from=price_list.valid_from,
                            valid_to=price_list.valid_to,
                            document_id=document.id,
                            item_id=item.id,
                        )
        elif document.type is DocumentType.INVOICE and document.totals is not None:
            sources.append(
                CostSource(
                    SourceKind.INVOICE,
                    document.date,
                    document.id,
                    amount=document.totals.gross,
                    currency=document.currency,
                )
            )
        else:
            sources.append(CostSource(SourceKind.CONTRACT, document.date, document.id))
    return CostItem(
        cost_ref=cost_ref,
        kind=CostKind.SUPPLIER,
        counterparty=counterparty.name,
        counterparty_id=counterparty.id,
        category=Category.VARIABLE_COST,
        currency=documents[0].currency,
        sources=tuple(sources),
        prices=tuple(prices.values()),
    )


def build_cost_memory(
    recurring: list[RecurringPayment] | tuple[RecurringPayment, ...],
    history: list[Transaction],
    dataset: ProofDataset,
    as_of: date,
) -> tuple[CostItem, ...]:
    """Fixed costs in the order RENT, UTIL, XFER, PAY, SOC, WTAX, then suppliers by cost_ref."""
    order = list(COST_REFS.values())
    fixed = sorted(
        (_fixed_item(p, history, dataset) for p in recurring if p.category in COST_REFS),
        key=lambda item: order.index(item.cost_ref),
    )
    by_supplier: dict[str, list[Document]] = defaultdict(list)
    for document in dataset.documents:
        if document.preloaded and document.cost_ref and document.cost_ref not in order:
            by_supplier[document.cost_ref].append(document)
    suppliers = [
        _supplier_item(ref, documents, dataset, as_of)
        for ref, documents in sorted(by_supplier.items())
    ]
    return (*fixed, *suppliers)
