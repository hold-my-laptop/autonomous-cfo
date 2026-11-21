"""Public shapes of Vero Proof (DATA_CONTRACTS §2.2): Cost Memory and documents."""

import datetime as dt
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field

from api.domain.models import Category
from api.domain.proof import cost_memory as cm
from api.domain.proof import dataset as ds
from api.money import DecimalStr, Money

Box = Annotated[
    list[float],
    Field(
        min_length=4,
        max_length=4,
        description="[x0, y0, x1, y1] in PDF points, origin top-left of the page",
    ),
]
DocumentSource = Literal["DOCUMENTS", "MAILBOX", "UPLOAD"]


class CostSource(BaseModel):
    kind: Literal["STATEMENT", "CONTRACT", "PRICE_LIST", "INVOICE"]
    date: dt.date = Field(description="Last payment seen (STATEMENT) or the document's date")
    document_id: str | None
    clause_id: str | None = Field(description="The clause that sets the amount (CONTRACT)")
    transaction_ids: list[UUID] = Field(description="The statement payments (STATEMENT)")
    amount: Money | None = Field(description="What this evidence says, in `currency`")
    currency: Literal["EUR", "CHF"] | None

    @classmethod
    def from_domain(cls, s: cm.CostSource) -> Self:
        return cls(
            kind=s.kind.value,
            date=s.date,
            document_id=s.document_id,
            clause_id=s.clause_id,
            transaction_ids=list(s.transaction_ids),
            amount=s.amount,
            currency=s.currency,
        )


class ExpectedPrice(BaseModel):
    description: str
    unit: str
    unit_price: DecimalStr = Field(description="Net price per unit in euros")
    vat_rate: int = Field(description="Basis points: 1900 = 19 %")
    valid_from: dt.date
    valid_to: dt.date | None
    document_id: str = Field(description="The price list")
    item_id: str

    @classmethod
    def from_domain(cls, p: cm.ExpectedPrice) -> Self:
        return cls(
            description=p.description,
            unit=p.unit,
            unit_price=p.unit_price,
            vat_rate=p.vat_rate,
            valid_from=p.valid_from,
            valid_to=p.valid_to,
            document_id=p.document_id,
            item_id=p.item_id,
        )


class CostItem(BaseModel):
    """What Vero expects one recurring cost to be, and the evidence for it."""

    cost_ref: str = Field(description="RENT, UTIL, XFER, PAY, SOC, WTAX or a supplier")
    kind: Literal["FIXED", "SUPPLIER"]
    counterparty: str
    counterparty_id: str | None
    category: Category
    currency: Literal["EUR", "CHF"]
    expected_amount: Money | None = Field(
        description="FIXED: the amount per payment in `currency` (the contract's when there is one)"
    )
    frequency: Literal["MONTHLY"] | None
    due_day: int | None
    eur_amount: Money | None = Field(
        description="CHF costs: the euro amount of the last payment, at `fx_rate`"
    )
    fx_rate: str | None
    recurring_id: UUID | None = Field(description="Phase 1's detected recurring payment")
    outflow_ref_prefix: str | None = Field(
        description='Prefix of its projected outflows, e.g. "RENT" for RENT-06 or "CHF" for CHF-06'
    )
    prices: list[ExpectedPrice] = Field(
        description="SUPPLIER: the prices of the price list valid today"
    )
    sources: list[CostSource]

    @classmethod
    def from_domain(cls, c: cm.CostItem) -> Self:
        return cls(
            cost_ref=c.cost_ref,
            kind=c.kind.value,
            counterparty=c.counterparty,
            counterparty_id=c.counterparty_id,
            category=c.category,
            currency=c.currency,
            expected_amount=c.expected_amount,
            frequency=c.frequency,
            due_day=c.due_day,
            eur_amount=c.eur_amount,
            fx_rate=c.fx_rate,
            recurring_id=c.recurring_id,
            outflow_ref_prefix=c.outflow_ref_prefix,
            prices=[ExpectedPrice.from_domain(p) for p in c.prices],
            sources=[CostSource.from_domain(s) for s in c.sources],
        )


class CounterpartyRef(BaseModel):
    id: str
    name: str


class DocumentSummary(BaseModel):
    id: str
    type: Literal[
        "LEASE", "CONTRACT", "INVOICE", "RENT_ADJUSTMENT", "NOTICE", "PRICE_LIST", "PAYMENT_PLAN"
    ]
    title: str
    title_en: str
    counterparty: CounterpartyRef
    date: dt.date
    language: Literal["de", "en", "it"]
    currency: Literal["EUR", "CHF"]
    document_number: str | None
    cost_ref: str | None
    source: DocumentSource = Field(
        description="DOCUMENTS (the owner's documents), MAILBOX (an email attachment, listed "
        "once a finding cites it) or UPLOAD"
    )
    page_count: int
    sha256: str | None
    file_url: str

    @classmethod
    def from_domain(cls, d: ds.Document, dataset: ds.ProofDataset) -> Self:
        counterparty = dataset.counterparty(d.counterparty_id)
        return cls(
            id=d.id,
            type=d.type.value,
            title=d.title,
            title_en=d.title_en,
            counterparty=CounterpartyRef(
                id=d.counterparty_id, name=counterparty.name if counterparty else d.counterparty_id
            ),
            date=d.date,
            language=d.language,
            currency=d.currency,
            document_number=d.document_number,
            cost_ref=d.cost_ref,
            source=d.source,
            page_count=len(d.pages),
            sha256=d.sha256,
            file_url=f"/api/v1/documents/{d.id}/file",
        )


class DocumentTextBlock(BaseModel):
    id: str
    text: str
    text_en: str
    box: Box | None


class DocumentPage(BaseModel):
    page: int
    width: float = Field(description="PDF points")
    height: float = Field(description="PDF points")
    blocks: list[DocumentTextBlock]


class DocumentLine(BaseModel):
    id: str
    description: str
    quantity: DecimalStr
    unit: str
    unit_price: DecimalStr
    vat_rate: int = Field(description="Basis points")
    net: Money
    gross: Money | None


class DocumentTotals(BaseModel):
    net: Money
    vat: Money
    gross: Money


class DocumentClause(BaseModel):
    id: str
    section: str
    title: str
    text: str
    text_en: str
    page: int
    box: Box | None
    parameters: dict[str, Any] = Field(
        description="The clause's terms as structured values (PROOF.md §4.3)"
    )


class DocumentPriceListItem(BaseModel):
    id: str
    description: str
    unit: str
    unit_price: DecimalStr
    vat_rate: int


class DocumentPriceList(BaseModel):
    valid_from: dt.date
    valid_to: dt.date | None
    items: list[DocumentPriceListItem]


class DocumentRentAdjustment(BaseModel):
    old_amount: Money
    new_amount: Money
    effective_from: dt.date
    frequency: Literal["MONTHLY"]


class DocumentHighlight(BaseModel):
    ref: str = Field(
        description="The value it marks, e.g. lines.l1.unit_price, totals.gross, "
        "rent_adjustment.new_amount, price_list.p1, date"
    )
    page: int
    text: str = Field(description="The value exactly as printed")
    box: Box


class Document(DocumentSummary):
    """A document with its pages, values and where each is printed, for side-by-side views."""

    pages: list[DocumentPage]
    lines: list[DocumentLine]
    totals: DocumentTotals | None
    clauses: list[DocumentClause]
    price_list: DocumentPriceList | None
    rent_adjustment: DocumentRentAdjustment | None
    highlights: list[DocumentHighlight]

    @classmethod
    def from_document(cls, d: ds.Document, dataset: ds.ProofDataset) -> Self:
        data = d.model_dump(mode="python", by_alias=False)
        return cls(
            **DocumentSummary.from_domain(d, dataset).model_dump(),
            pages=data["pages"],
            lines=data["lines"],
            totals=data["totals"],
            clauses=data["clauses"],
            price_list=data["price_list"],
            rent_adjustment=data["rent_adjustment"],
            highlights=data["highlights"],
        )
