"""Vero Proof findings: one difference and everything Vero did about it (docs/PROOF.md §3).

These are also the public shapes (schema name ProofFinding: Phase 1 already has a Finding), so
they serialize with the API's conventions: money as cent strings, unit prices as decimals."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from api.money import DecimalStr, Money


class Classification(StrEnum):
    VERIFIED = "VERIFIED"
    UNEXPLAINED = "UNEXPLAINED"
    CONFLICT = "CONFLICT"


class FindingStatus(StrEnum):
    INGESTED = "INGESTED"
    EXTRACTED = "EXTRACTED"
    CHECKED = "CHECKED"
    INVESTIGATING = "INVESTIGATING"
    CLASSIFIED = "CLASSIFIED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    RESOLVED = "RESOLVED"


class FindingKind(StrEnum):
    INVOICE_PRICES = "INVOICE_PRICES"
    RECURRING_AMOUNT = "RECURRING_AMOUNT"


class DifferenceKind(StrEnum):
    PRICE_CHANGE = "PRICE_CHANGE"
    NEW_ITEM = "NEW_ITEM"
    NEW_CHARGE = "NEW_CHARGE"
    AMOUNT_CHANGE = "AMOUNT_CHANGE"


class EvidenceKind(StrEnum):
    DOCUMENT = "DOCUMENT"
    INVOICE_LINE = "INVOICE_LINE"
    PRICE_LIST_ITEM = "PRICE_LIST_ITEM"
    CLAUSE = "CLAUSE"
    TRANSACTION = "TRANSACTION"
    EMAIL = "EMAIL"
    COST_ITEM = "COST_ITEM"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceRef(_Frozen):
    kind: EvidenceKind
    document_id: str | None = None
    item_id: str | None = Field(
        default=None, description="Line, price list item or clause id inside the document"
    )
    transaction_id: UUID | None = None
    cost_ref: str | None = None


class FindingDifference(_Frozen):
    """One line or amount that differs from what Cost Memory expects."""

    kind: DifferenceKind
    description: str
    line_id: str | None = None
    previous_unit_price: DecimalStr | None = None
    unit_price: DecimalStr | None = None
    quantity: DecimalStr | None = None
    expected_amount: Money | None = Field(default=None, description="Amounts: what was expected")
    billed_amount: Money | None = None
    delta: Money = Field(description="Effect in cents on this document or payment")
    bps: int | None = Field(default=None, description="Change in basis points, when it has one")
    explained: bool
    evidence: tuple[EvidenceRef, ...] = ()


class FindingImpact(_Frozen):
    currency: str
    expected: Money | None = Field(description="What Cost Memory expected (amounts)")
    billed: Money | None
    delta: Money = Field(description="Billed minus expected, in cents (per payment for rent)")
    bps: int | None


class FindingHistoryEntry(_Frozen):
    at: datetime
    status: FindingStatus
    classification: Classification | None = None
    label: str


class ProofFinding(_Frozen):
    id: UUID
    title: str = Field(description="One line for lists, e.g. the counterparty and the change")
    kind: FindingKind
    classification: Classification
    escalated: bool = Field(
        default=False,
        description="UNEXPLAINED after an investigation that could not find a reason",
    )
    status: FindingStatus
    counterparty: str
    counterparty_id: str | None
    cost_ref: str
    period_from: date | None = Field(description="Billing period or the change's start")
    period_to: date | None = None
    document_id: str | None = Field(description="The document that billed it")
    previous_document_id: str | None = Field(default=None, description="What it was compared with")
    transaction_id: UUID | None = Field(default=None, description="A statement payment")
    differences: tuple[FindingDifference, ...]
    impact: FindingImpact
    evidence_refs: tuple[EvidenceRef, ...] = ()
    propagation: dict[str, Any] | None = None
    recommendation_ids: tuple[UUID, ...] = ()
    run_id: UUID | None = None
    acknowledged_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    history: tuple[FindingHistoryEntry, ...]

    @model_validator(mode="before")
    @classmethod
    def _ignore_computed(cls, data: Any) -> Any:
        """`quiet` and `in_digest` are derived; a stored or replayed copy may carry them."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in ("quiet", "in_digest")}
        return data

    @computed_field
    @property
    def quiet(self) -> bool:
        """Handled without the owner: verified, nothing to decide."""
        return (
            self.classification is Classification.VERIFIED
            and self.status is FindingStatus.CLASSIFIED
            and not self.recommendation_ids
        )

    @computed_field
    @property
    def in_digest(self) -> bool:
        """Shown until the owner acknowledges it (quiet) or decides its actions (RESOLVED)."""
        return self.acknowledged_at is None and self.status is not FindingStatus.RESOLVED

    def advance(
        self,
        at: datetime,
        status: FindingStatus,
        label: str,
        classification: Classification | None = None,
        **changes: Any,
    ) -> Self:
        """The next state, with the transition recorded in the history."""
        entry = FindingHistoryEntry(
            at=at, status=status, classification=classification, label=label
        )
        update = {
            "status": status,
            "updated_at": at,
            "history": (*self.history, entry),
            **changes,
        }
        if classification is not None:
            update["classification"] = classification
        return self.model_copy(update=update)

    def stored(self) -> dict[str, Any]:
        """JSON for storage: the fields only, without the computed ones."""
        return self.model_dump(mode="json", exclude={"quiet", "in_digest"})
