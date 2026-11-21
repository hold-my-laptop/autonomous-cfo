"""The grounding check and the final label of an investigation (docs/PROOF.md §5.6).

The agent proposes; this code decides. A citation counts only if a tool returned it in this
run. The label rests on one `compute` call, named by the completion: each of its inputs must
come from Cost Memory, the billing document or the finding, or from a tool result earlier in
the run of the kind the input needs: what the cost was before the change (never the billed
amount), dates of the document, index values from `get_reference_index`, clause terms from
`read_clause`'s parameters. The formula is then recomputed from those inputs and the
billed amount compared with the justified one; numbers written in the completion are never
used."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from api.domain.forecast import ForecastInput
from api.domain.proof.cost_memory import CostItem
from api.domain.proof.dataset import Document, ProofDataset
from api.domain.proof.engine import FormulaError, compute
from api.domain.proof.findings import (
    Classification,
    DifferenceKind,
    EvidenceKind,
    EvidenceRef,
    FindingDifference,
    FindingInvestigation,
    FindingKind,
    FindingStatus,
    InvestigationMode,
    ProofFinding,
)
from api.money import Cents, format_eur


class Source(StrEnum):
    """EXPECTED: what the cost was before the change (Cost Memory, the old amount, the goods
    without a new charge), never the billed amount."""

    EXPECTED = "EXPECTED"
    DATE = "DATE"
    INDEX = "INDEX"
    CLAUSE = "CLAUSE"


@dataclass(frozen=True, slots=True)
class Call:
    seq: int
    call_id: str
    tool: str
    arguments: dict[str, Any]
    label: str | None
    ok: bool
    result: Any


def calls_of(tool_log: dict | None) -> list[Call]:
    """The run's tool calls in the order they were made."""
    calls = [
        Call(
            seq=entry.get("seq", 0),
            call_id=call_id,
            tool=entry.get("tool", ""),
            arguments=entry.get("arguments") or {},
            label=entry.get("label"),
            ok=bool(entry["response"].get("ok")),
            result=entry["response"].get("result"),
        )
        for call_id, entry in ((tool_log or {}).get("calls") or {}).items()
    ]
    return sorted(calls, key=lambda c: c.seq)


def searched(calls: Iterable[Call]) -> tuple[str, ...]:
    return tuple(c.label for c in calls if c.label)


@dataclass(frozen=True, slots=True)
class Rule:
    """How a formula's result labels a finding: the source each input needs, what was
    billed for the change it covers, and what the outputs allow."""

    inputs: dict[str, frozenset[Source]]
    covers: frozenset[DifferenceKind]
    billed: Callable[[ProofFinding], Cents | None]
    justified: Callable[[dict[str, Any]], Cents]


def _recurring_billed(finding: ProofFinding) -> Cents | None:
    return finding.impact.billed if finding.kind is FindingKind.RECURRING_AMOUNT else None


def _charges_billed(finding: ProofFinding) -> Cents | None:
    charges = [
        d for d in finding.differences if d.kind is DifferenceKind.NEW_CHARGE and not d.explained
    ]
    return sum(d.delta for d in charges) if charges else None


_TERM = frozenset({Source.CLAUSE})
RULES: dict[str, Rule] = {
    "vpi_threshold_indexation": Rule(
        inputs={
            "base_amount": frozenset({Source.EXPECTED, Source.CLAUSE}),
            "base_index": frozenset({Source.INDEX}),
            "current_index": frozenset({Source.INDEX}),
            "threshold_bps": _TERM,
        },
        covers=frozenset({DifferenceKind.AMOUNT_CHANGE}),
        billed=_recurring_billed,
        justified=lambda outputs: int(outputs["justified"]),
    ),
    "surcharge_rule": Rule(
        inputs={
            "net_goods": frozenset({Source.EXPECTED}),
            "date": frozenset({Source.DATE}),
            "surcharge": _TERM,
            "below_net_goods": _TERM,
            "valid_from": _TERM,
        },
        covers=frozenset({DifferenceKind.NEW_CHARGE}),
        billed=_charges_billed,
        justified=lambda outputs: int(outputs["surcharge"]),
    ),
}


def canon(value: Any) -> str | None:
    """One spelling per value, so 380000, "380000" and "380000.00" compare equal."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int | Decimal | str):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return str(value)
        return format(number.normalize(), "f") if number.is_finite() else str(value)
    return None


def _flatten(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _flatten(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _flatten(item)
    else:
        yield value


Sources = dict[Source, set[str]]


def _add(sources: Sources, kind: Source, *values: Any) -> None:
    sources[kind] |= {c for c in (canon(v) for v in values) if c is not None}


def base_sources(
    finding: ProofFinding, cost_item: CostItem | None, document: Document | None
) -> Sources:
    """Values the backend already holds: Cost Memory, the finding and the billing document."""
    sources: Sources = {kind: set() for kind in Source}
    if cost_item is not None:
        _add(sources, Source.EXPECTED, cost_item.expected_amount, cost_item.eur_amount)
    _add(sources, Source.EXPECTED, finding.impact.expected)
    _add(sources, Source.EXPECTED, *(d.expected_amount for d in finding.differences))
    _add(sources, Source.DATE, finding.period_from, finding.period_to)
    if document is not None:
        _add(sources, Source.DATE, document.date)
        if document.rent_adjustment:
            adjustment = document.rent_adjustment
            _add(sources, Source.EXPECTED, adjustment.old_amount)
            _add(sources, Source.DATE, adjustment.effective_from)
    return sources


def _tool_sources(calls: Iterable[Call]) -> Sources:
    sources: Sources = {kind: set() for kind in Source}
    for call in calls:
        if not call.ok or not isinstance(call.result, dict):
            continue
        if call.tool == "get_reference_index":
            _add(sources, Source.INDEX, *(v.get("value") for v in call.result.get("values", [])))
        elif call.tool == "read_clause":
            _add(sources, Source.CLAUSE, *_flatten(call.result.get("parameters") or {}))
    return sources


def evidence_ref(
    evidence_id: str, proof: ProofDataset, documents: dict[str, Document]
) -> EvidenceRef | None:
    """The finding's evidence for an id a tool returned."""
    if "#" in evidence_id:
        document_id, item_id = evidence_id.split("#", 1)
        document = documents.get(document_id)
        if document is None:
            return None
        if document.clause(item_id):
            kind = EvidenceKind.CLAUSE
        elif document.price_list and any(i.id == item_id for i in document.price_list.items):
            kind = EvidenceKind.PRICE_LIST_ITEM
        else:
            return None
        return EvidenceRef(kind=kind, document_id=document_id, item_id=item_id, ref=evidence_id)
    if evidence_id in documents:
        return EvidenceRef(kind=EvidenceKind.DOCUMENT, document_id=evidence_id, ref=evidence_id)
    if any(e.id == evidence_id for e in proof.mailbox):
        return EvidenceRef(kind=EvidenceKind.EMAIL, ref=evidence_id)
    if any(s.id == evidence_id for s in proof.reference_values):
        return EvidenceRef(kind=EvidenceKind.REFERENCE_VALUE, ref=evidence_id)
    return None


@dataclass(frozen=True, slots=True)
class Grounding:
    classification: Classification
    investigation: FindingInvestigation
    evidence: tuple[EvidenceRef, ...]
    differences: tuple[FindingDifference, ...]
    steps: tuple[str, ...]

    @property
    def escalated(self) -> bool:
        return self.classification is Classification.UNEXPLAINED

    @property
    def history_label(self) -> str:
        result = self.investigation
        if self.classification is Classification.CONFLICT:
            return (
                f"Conflict: {format_eur(result.billed)} billed, "
                f"{format_eur(result.justified)} allowed"
            )
        if self.classification is Classification.VERIFIED:
            return f"Verified: {format_eur(result.justified)} allowed by the evidence"
        return "No usable evidence: needs your review"

    def apply(self, finding: ProofFinding, at: datetime) -> ProofFinding:
        """The finding with this label, its investigation and the evidence it cites."""
        return finding.advance(
            at,
            FindingStatus.CLASSIFIED,
            self.history_label,
            self.classification,
            escalated=self.escalated,
            investigation=self.investigation,
            differences=self.differences,
            evidence_refs=tuple(dict.fromkeys((*finding.evidence_refs, *self.evidence))),
        )


def ground(
    finding: ProofFinding,
    completion: dict[str, Any],
    calls: list[Call],
    sources: Sources,
    proof: ProofDataset,
    documents: dict[str, Document],
    forecast: ForecastInput,
    mode: InvestigationMode = InvestigationMode.AGENT,
) -> Grounding:
    """Check the completion against this run's tool calls and set the label."""
    returned = {
        evidence_id
        for call in calls
        if call.ok and isinstance(call.result, dict)
        for evidence_id in call.result.get("evidence_ids", [])
    }
    cited = list(dict.fromkeys(completion.get("evidence_refs") or []))
    kept = [r for r in cited if r in returned]
    dropped = tuple(r for r in cited if r not in returned)
    steps: list[str] = []
    if dropped:
        count = len(dropped)
        steps.append(
            f"Dropped {count} citation{'s' if count != 1 else ''} no tool returned in this run: "
            + ", ".join(dropped[:5])
        )
    evidence = tuple(ref for ref in (evidence_ref(r, proof, documents) for r in kept) if ref)

    problems: list[str] = []
    result = None
    rule = None
    billed = None
    call_id = completion.get("compute_call_id")
    call = next((c for c in calls if c.call_id == call_id), None) if call_id else None
    if not call_id:
        problems.append(
            "the answer names no compute call"
            if mode is InvestigationMode.AGENT
            else "no clause and reference value explain the change"
        )
    elif call is None or call.tool != "compute":
        problems.append(f"no compute call {call_id!r} in this run")
    elif not call.ok:
        problems.append(f"compute call {call_id!r} failed")
    else:
        formula_id = str(call.arguments.get("formula_id"))
        inputs = call.arguments.get("inputs") or {}
        rule = RULES.get(formula_id)
        billed = rule.billed(finding) if rule else None
        if rule is None:
            problems.append(f"{formula_id} does not decide a label")
        elif billed is None:
            problems.append(f"{formula_id} does not fit this finding")
        else:
            earlier = _tool_sources(c for c in calls if c.seq < call.seq)
            for field, kinds in rule.inputs.items():
                value = canon(inputs.get(field))
                if value is None or not any(value in sources[k] | earlier[k] for k in kinds):
                    problems.append(
                        f"{field} {inputs.get(field)!r} has no source in Cost Memory, the "
                        "document or an earlier tool result"
                    )
            if not problems:
                try:
                    result = compute(formula_id, inputs, forecast)
                except FormulaError as exc:
                    problems.append(str(exc))

    differences = finding.differences
    justified = None
    if result is None:
        classification = Classification.UNEXPLAINED
        steps.append(f"Can't use the answer: {'; '.join(problems[:3])}")
    else:
        outputs = result.outputs.model_dump(mode="json")
        justified = rule.justified(outputs)
        steps.append(f"Recomputed: {format_eur(justified)} allowed, {format_eur(billed)} billed")
        if billed > justified:
            classification = Classification.CONFLICT
        else:
            classification = Classification.VERIFIED
            differences = tuple(
                d.model_copy(update={"explained": True, "evidence": (*d.evidence, *evidence)})
                if d.kind in rule.covers and not d.explained
                else d
                for d in differences
            )
            if any(not d.explained for d in differences):
                classification = Classification.UNEXPLAINED
                problems.append("other differences on this document are still unexplained")
                differences = finding.differences
                steps.append("Other differences on this document are still unexplained")
    proposed = completion.get("classification")
    if proposed and proposed != classification.value:
        steps.append(
            f"Vero's agent proposed {proposed.lower()}; the backend's check says "
            f"{classification.value.lower()}"
        )
    investigation = FindingInvestigation(
        mode=mode,
        formula_id=result.formula_id if result else None,
        engine_version=result.engine_version if result else None,
        inputs=result.inputs.model_dump(mode="json") if result else None,
        outputs=result.outputs.model_dump(mode="json") if result else None,
        billed=billed if result else None,
        justified=justified,
        disputed=billed - justified if result and billed > justified else None,
        searched=searched(calls),
        dropped_refs=dropped,
        problems=tuple(problems),
        note=completion.get("explanation"),
        proposed=Classification(proposed) if proposed else None,
    )
    return Grounding(classification, investigation, evidence, differences, tuple(steps))
