"""CHECK (docs/PROOF.md §5.4): billed amounts and unit prices against Cost Memory.

Deterministic, no model. A difference is explained here only by a stored price list giving
exactly the new price; CHECK never reads a clause, which is the investigation's job. Results:
no finding, a VERIFIED finding (known reason) or an UNEXPLAINED one (to investigate)."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from api.domain.models import Transaction
from api.domain.proof.cost_memory import CostItem, CostKind, SourceKind
from api.domain.proof.dataset import (
    Document,
    DocumentType,
    InvoiceLineCheck,
    LineCheck,
    ProofDataset,
    compare_invoice,
    line_net,
)
from api.domain.proof.engine import compute
from api.domain.proof.findings import (
    Classification,
    DifferenceKind,
    EvidenceKind,
    EvidenceRef,
    FindingDifference,
    FindingHistoryEntry,
    FindingImpact,
    FindingKind,
    FindingStatus,
    ProofFinding,
)
from api.money import Cents, Money, format_eur

AMOUNT_TOLERANCE_PERCENT = 2
_FINDING_NAMESPACE = UUID("8c1f5a2e-3d4b-4e6f-9a7b-0c1d2e3f4a5b")


class RoutineCheck(BaseModel):
    """A statement payment of a recurring cost compared with the contract behind it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cost_ref: str
    counterparty: str
    transaction_id: UUID
    date: date
    paid: Money
    expected: Money
    currency: str
    eur_amount: Money | None = None
    fx_rate: str | None = None
    document_id: str
    clause_id: str | None
    matches: bool


def finding_id(key: str) -> UUID:
    """Stable ids, so a reset recreates the preloaded findings identically."""
    return uuid5(_FINDING_NAMESPACE, key)


def within_tolerance(billed: Cents, expected: Cents) -> bool:
    """Phase 1's recurring tolerance: at most 2 % away."""
    return abs(billed - expected) * 100 <= AMOUNT_TOLERANCE_PERCENT * abs(expected)


def _bps(billed: Cents, expected: Cents) -> int | None:
    if expected <= 0:
        return None
    variance = compute("variance", {"billed": billed, "previous": expected})
    return variance.outputs.bps


def _day(d: date) -> str:
    return f"{d.day} {d:%b} {d.year}"


def _price(value: Decimal) -> str:
    return format_eur(line_net(Decimal(1), value))


def _received(
    document: Document, counterparty: str, at: datetime
) -> tuple[FindingHistoryEntry, ...]:
    number = f" {document.document_number}" if document.document_number else ""
    lines = f"{len(document.lines)} lines" if document.lines else "the amounts"
    return (
        FindingHistoryEntry(
            at=at,
            status=FindingStatus.INGESTED,
            label=f"{document.title_en}{number} from {counterparty}, dated {_day(document.date)}",
        ),
        FindingHistoryEntry(at=at, status=FindingStatus.EXTRACTED, label=f"Read {lines}"),
    )


def _line_difference(document: Document, check: InvoiceLineCheck) -> FindingDifference:
    line = next(item for item in document.lines if item.id == check.line_id)
    evidence = [
        EvidenceRef(kind=EvidenceKind.INVOICE_LINE, document_id=document.id, item_id=line.id)
    ]
    if check.price_list_id:
        evidence.append(
            EvidenceRef(
                kind=EvidenceKind.PRICE_LIST_ITEM,
                document_id=check.price_list_id,
                item_id=check.price_list_item_id,
            )
        )
    explained = check.result is LineCheck.PRICE_LIST
    if check.previous_unit_price is not None:
        kind = DifferenceKind.PRICE_CHANGE
        delta = line.net - line_net(line.quantity, check.previous_unit_price)
    else:
        kind = DifferenceKind.NEW_ITEM if explained else DifferenceKind.NEW_CHARGE
        delta = 0 if explained else line.net
    return FindingDifference(
        kind=kind,
        description=line.description,
        line_id=line.id,
        previous_unit_price=check.previous_unit_price,
        unit_price=line.unit_price,
        quantity=line.quantity,
        delta=delta,
        explained=explained,
        evidence=tuple(evidence),
    )


def _evidence(document: Document, extra: list[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    refs = [EvidenceRef(kind=EvidenceKind.DOCUMENT, document_id=document.id), *extra]
    unique: list[EvidenceRef] = []
    for ref in refs:
        if ref not in unique:
            unique.append(ref)
    return tuple(unique)


def check_invoice(
    document: Document,
    previous: Document | None,
    price_lists: list[Document],
    counterparty: str,
    cost_ref: str,
    at: datetime,
    new_id: UUID,
) -> ProofFinding | None:
    """Unit prices against the supplier's previous invoice, then the price list valid on the
    invoice date. The supplier's first invoice is the baseline: no finding."""
    if previous is None:
        return None
    usable = [pl for pl in price_lists if pl.date <= document.date]
    checks = compare_invoice(previous, document, usable)
    if not checks:
        return None
    differences = tuple(_line_difference(document, c) for c in checks)
    unexplained = [d for d in differences if not d.explained]
    delta = sum(d.delta for d in differences)
    net = document.totals.net if document.totals else sum(line.net for line in document.lines)
    impact = FindingImpact(
        currency=document.currency,
        expected=net - delta,
        billed=net,
        delta=delta,
        bps=_bps(net, net - delta),
    )
    count = "1 line differs" if len(differences) == 1 else f"{len(differences)} lines differ"
    compared = f"Compared with {previous.document_number}: {count}"
    received = _received(document, counterparty, at)
    evidence = _evidence(
        document,
        [EvidenceRef(kind=EvidenceKind.DOCUMENT, document_id=previous.id)]
        + [ref for d in differences for ref in d.evidence],
    )
    common = {
        "id": new_id,
        "kind": FindingKind.INVOICE_PRICES,
        "counterparty": counterparty,
        "counterparty_id": document.counterparty_id,
        "cost_ref": cost_ref,
        "period_from": document.date,
        "period_to": document.date,
        "document_id": document.id,
        "previous_document_id": previous.id,
        "differences": differences,
        "impact": impact,
        "evidence_refs": evidence,
        "created_at": at,
        "updated_at": at,
    }
    if unexplained:
        names = ", ".join(f"{d.description} ({_price(d.unit_price)})" for d in unexplained)
        return ProofFinding(
            **common,
            title=f"{counterparty}: {names} on {document.document_number}",
            classification=Classification.UNEXPLAINED,
            status=FindingStatus.CHECKED,
            history=(
                *received,
                FindingHistoryEntry(
                    at=at,
                    status=FindingStatus.CHECKED,
                    classification=Classification.UNEXPLAINED,
                    label=f"{compared}; not on any price list: {names}",
                ),
            ),
        )
    lists = sorted({c.price_list_id for c in checks if c.price_list_id})
    titles = ", ".join(pl.title_en for pl in price_lists if pl.id in lists)
    return ProofFinding(
        **common,
        title=f"{counterparty}: new prices on {document.document_number}",
        classification=Classification.VERIFIED,
        status=FindingStatus.CLASSIFIED,
        history=(
            *received,
            FindingHistoryEntry(at=at, status=FindingStatus.CHECKED, label=compared),
            FindingHistoryEntry(
                at=at,
                status=FindingStatus.CLASSIFIED,
                classification=Classification.VERIFIED,
                label=f"Every difference matches the {titles}",
            ),
        ),
    )


def check_rent_adjustment(
    document: Document, item: CostItem, at: datetime, new_id: UUID
) -> ProofFinding | None:
    """The new amount from `effective_from` is the billed amount of the recurring cost."""
    change = document.rent_adjustment
    if change is None or item.expected_amount is None:
        return None
    billed, expected = change.new_amount, item.expected_amount
    if within_tolerance(billed, expected):
        return None
    bps = _bps(billed, expected)
    difference = FindingDifference(
        kind=DifferenceKind.AMOUNT_CHANGE,
        description=f"Monthly {item.cost_ref.lower()} from {_day(change.effective_from)}",
        expected_amount=expected,
        billed_amount=billed,
        delta=billed - expected,
        bps=bps,
        explained=False,
        evidence=(
            EvidenceRef(kind=EvidenceKind.DOCUMENT, document_id=document.id),
            EvidenceRef(kind=EvidenceKind.COST_ITEM, cost_ref=item.cost_ref),
        ),
    )
    percent = f"{Decimal(bps or 0) / 100:+.2f} %".replace(".", ",")
    label = (
        f"{format_eur(billed)} from {_day(change.effective_from)} against "
        f"{format_eur(expected)} expected: {percent}"
    )
    return ProofFinding(
        id=new_id,
        title=f"{item.counterparty}: {item.cost_ref.lower()} {format_eur(expected)} → "
        f"{format_eur(billed)} from {_day(change.effective_from)}",
        kind=FindingKind.RECURRING_AMOUNT,
        classification=Classification.UNEXPLAINED,
        status=FindingStatus.CHECKED,
        counterparty=item.counterparty,
        counterparty_id=item.counterparty_id,
        cost_ref=item.cost_ref,
        period_from=change.effective_from,
        document_id=document.id,
        differences=(difference,),
        impact=FindingImpact(
            currency=item.currency,
            expected=expected,
            billed=billed,
            delta=billed - expected,
            bps=bps,
        ),
        evidence_refs=difference.evidence,
        created_at=at,
        updated_at=at,
        history=(
            *_received(document, item.counterparty, at),
            FindingHistoryEntry(
                at=at,
                status=FindingStatus.CHECKED,
                classification=Classification.UNEXPLAINED,
                label=label,
            ),
        ),
    )


def check_document(
    document: Document,
    dataset: ProofDataset,
    cost_memory: tuple[CostItem, ...],
    checked: list[Document],
    at: datetime,
    new_id: UUID,
) -> ProofFinding | None:
    """CHECK one billing document. `checked` are the documents already checked, for the
    supplier's previous invoice."""
    item = next((c for c in cost_memory if c.cost_ref == document.cost_ref), None)
    counterparty = dataset.counterparty(document.counterparty_id)
    name = counterparty.name if counterparty else document.counterparty_id
    if document.type is DocumentType.RENT_ADJUSTMENT:
        return None if item is None else check_rent_adjustment(document, item, at, new_id)
    if document.type is not DocumentType.INVOICE:
        return None
    earlier = [
        d
        for d in checked
        if d.type is DocumentType.INVOICE
        and d.counterparty_id == document.counterparty_id
        and (d.date, d.id) < (document.date, document.id)
    ]
    previous = max(earlier, key=lambda d: (d.date, d.id), default=None)
    price_lists = [
        d
        for d in dataset.documents
        if d.preloaded
        and d.price_list is not None
        and d.counterparty_id == document.counterparty_id
    ]
    cost_ref = document.cost_ref or (item.cost_ref if item else document.counterparty_id)
    return check_invoice(document, previous, price_lists, name, cost_ref, at, new_id)


def routine_checks(
    cost_memory: tuple[CostItem, ...], history: list[Transaction], at: datetime
) -> tuple[tuple[RoutineCheck, ...], list[ProofFinding]]:
    """Each statement payment of a fixed cost that has a contract behind it, compared with
    the contract amount (a CHF cost in CHF). A match is a routine check, not a finding."""
    checks: list[RoutineCheck] = []
    findings: list[ProofFinding] = []
    for item in cost_memory:
        contract = next((s for s in item.sources if s.kind is SourceKind.CONTRACT), None)
        if item.kind is not CostKind.FIXED or contract is None or item.expected_amount is None:
            continue
        payments = [
            t
            for t in history
            if t.amount < 0 and t.category is item.category and t.counterparty == item.counterparty
        ]
        for t in sorted(payments, key=lambda t: (t.booking_date, str(t.id))):
            foreign = item.currency != "EUR" and t.original_amount is not None
            paid = -(t.original_amount if foreign else t.amount)
            check = RoutineCheck(
                cost_ref=item.cost_ref,
                counterparty=item.counterparty,
                transaction_id=t.id,
                date=t.booking_date,
                paid=paid,
                expected=item.expected_amount,
                currency=item.currency,
                eur_amount=-t.amount if foreign else None,
                fx_rate=t.fx_rate if foreign else None,
                document_id=contract.document_id or "",
                clause_id=contract.clause_id,
                matches=within_tolerance(paid, item.expected_amount),
            )
            checks.append(check)
            if not check.matches:
                findings.append(_payment_finding(item, check, at))
    return tuple(checks), findings


def _payment_finding(item: CostItem, check: RoutineCheck, at: datetime) -> ProofFinding:
    bps = _bps(check.paid, check.expected)
    evidence = (
        EvidenceRef(kind=EvidenceKind.TRANSACTION, transaction_id=check.transaction_id),
        EvidenceRef(
            kind=EvidenceKind.CLAUSE, document_id=check.document_id, item_id=check.clause_id
        ),
    )
    label = (
        f"Paid {format_eur(check.paid)} on {_day(check.date)} against "
        f"{format_eur(check.expected)} in the contract"
    )
    return ProofFinding(
        id=finding_id(f"payment|{check.transaction_id}"),
        title=f"{item.counterparty}: {format_eur(check.paid)} paid, "
        f"{format_eur(check.expected)} in the contract",
        kind=FindingKind.RECURRING_AMOUNT,
        classification=Classification.UNEXPLAINED,
        status=FindingStatus.CHECKED,
        counterparty=item.counterparty,
        counterparty_id=item.counterparty_id,
        cost_ref=item.cost_ref,
        period_from=check.date,
        document_id=None,
        transaction_id=check.transaction_id,
        differences=(
            FindingDifference(
                kind=DifferenceKind.AMOUNT_CHANGE,
                description=f"Payment on {_day(check.date)}",
                expected_amount=check.expected,
                billed_amount=check.paid,
                delta=check.paid - check.expected,
                bps=bps,
                explained=False,
                evidence=evidence,
            ),
        ),
        impact=FindingImpact(
            currency=check.currency,
            expected=check.expected,
            billed=check.paid,
            delta=check.paid - check.expected,
            bps=bps,
        ),
        evidence_refs=evidence,
        created_at=at,
        updated_at=at,
        history=(
            FindingHistoryEntry(at=at, status=FindingStatus.INGESTED, label="Statement payment"),
            FindingHistoryEntry(
                at=at,
                status=FindingStatus.CHECKED,
                classification=Classification.UNEXPLAINED,
                label=label,
            ),
        ),
    )


def preloaded_findings(
    dataset: ProofDataset,
    cost_memory: tuple[CostItem, ...],
    history: list[Transaction],
    at: datetime,
) -> tuple[tuple[ProofFinding, ...], tuple[RoutineCheck, ...]]:
    """CHECK at load and reset: the preloaded billing documents in date order (so each
    invoice is compared with the one just before it), then the statement payments."""
    billing = sorted(
        (
            d
            for d in dataset.documents
            if d.preloaded and d.type in (DocumentType.INVOICE, DocumentType.RENT_ADJUSTMENT)
        ),
        key=lambda d: (d.date, d.id),
    )
    findings: list[ProofFinding] = []
    checked: list[Document] = []
    for document in billing:
        found = check_document(
            document, dataset, cost_memory, checked, at, finding_id(f"document|{document.id}")
        )
        if found is not None:
            findings.append(found)
        checked.append(document)
    routine, mismatches = routine_checks(cost_memory, history, at)
    return (*findings, *mismatches), routine
