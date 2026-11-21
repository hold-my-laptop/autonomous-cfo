"""The scripted investigation (FALLBACK, docs/PROOF.md §5.5): fixed hypotheses in a fixed order,
through the same tools as the agent, ending in the same grounding check.

- H1, a letter or notice: the inbox, searched for the change from that counterparty.
- H2, a clause that allows it: for a recurring amount, the clauses of its contract in Cost
  Memory with an index rule; for a new charge on an invoice, the clauses of the documents
  the found announcement brought (a new charge needs an announcement).
- H3, reference values: the index at the clause's base period and the latest one, then the
  clause's formula through `compute`.
- H4, quantity or scope: no scripted step yet; such a change ends escalated.

It is used when the agent can't finish and, at load and reset, for the preloaded findings
that CHECK left unexplained."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from api.domain.proof.cost_memory import SourceKind
from api.domain.proof.findings import (
    Classification,
    DifferenceKind,
    FindingKind,
    FindingStatus,
    InvestigationMode,
    ProofFinding,
)
from api.domain.proof.grounding import Call, Grounding, base_sources, ground
from api.proof_tools import PROOF_TOOLS
from api.rag import terms
from api.tool_base import ToolError

if TYPE_CHECKING:
    from api.domain.proof.dataset import Clause, Document
    from api.state import DemoState

_QUERY_WORDS = {"RENT": "rent", "UTILITIES": "utilities", "PARTNER_TRANSFER": "transfer"}


@dataclass
class _Script:
    state: DemoState
    calls: list[Call] = field(default_factory=list)
    steps: list[tuple[str, str]] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)

    def hypothesis(self, label: str) -> None:
        self.steps.append(("INVESTIGATE", label))

    def call(self, name: str, arguments: dict[str, Any]) -> Call:
        tool = PROOF_TOOLS[name]
        seq = len(self.calls) + 1
        try:
            output = tool.handler(self.state, None, tool.args.model_validate(arguments), None)
        except (ToolError, ValidationError) as exc:
            message = exc.message if isinstance(exc, ToolError) else "invalid arguments"
            call = Call(
                seq, f"script-{seq}", name, arguments, f"Tool {name}: {message}", False, None
            )
            self.steps.append(("TOOL_CALL", call.label))
        else:
            call = Call(seq, f"script-{seq}", name, arguments, output.label, True, output.result)
            self.steps.append(("TOOL_CALL", output.label))
            if output.result_label:
                self.steps.append(("TOOL_RESULT", output.result_label))
        self.calls.append(call)
        return call

    def cite(self, *evidence_ids: str) -> None:
        self.cited += [e for e in evidence_ids if e not in self.cited]


def _email_text(email: dict[str, Any]) -> str:
    blocks = [
        block["text"]
        for attachment in email["attachments"]
        for page in attachment["pages"]
        for block in page["blocks"]
    ]
    return " ".join([email["subject"], email["body"], *blocks])


def _letter(script: _Script, finding: ProofFinding, query: str) -> list[Document]:
    """H1: the best-matching email from the counterparty, if it speaks of every term of the
    query (a letter about the rent that doesn't mention an increase is not the notice); cites
    it and its attachments and returns the attached documents."""
    script.hypothesis("H1 · A letter or notice about the change")
    found = script.call("search_inbox", {"query": query, "counterparty": finding.counterparty_id})
    emails = found.result.get("emails", []) if found.ok else []
    wanted = terms(query)
    letter = next((e for e in emails if wanted <= terms(_email_text(e))), None)
    if letter is None:
        return []
    attached = [a["document_id"] for a in letter["attachments"]]
    script.cite(letter["email_id"], *attached)
    proof = script.state.proof
    return [d for d in (proof.document(i) for i in attached) if d]


def _read(script: _Script, document: Document, clause: Clause) -> dict[str, Any] | None:
    read = script.call("read_clause", {"doc_id": document.id, "section": clause.section})
    if not read.ok:
        return None
    script.cite(read.result["clause_ref"])
    return read.result["parameters"]


def _recurring(script: _Script, finding: ProofFinding) -> str | None:
    """A changed recurring amount: the notice, the contract's index clause, the index."""
    state = script.state
    item = next((c for c in state.cost_memory if c.cost_ref == finding.cost_ref), None)
    word = _QUERY_WORDS.get(item.category.value if item else "", finding.cost_ref.lower())
    _letter(script, finding, f"{word} increase")

    script.hypothesis("H2 · A contract clause that allows it")
    contracts = [
        state.proof.document(s.document_id)
        for s in (item.sources if item else ())
        if s.kind is SourceKind.CONTRACT and s.document_id
    ]
    rules = [
        (document, clause)
        for document in contracts
        if document
        for clause in document.clauses
        if {"index", "base_period", "threshold_bps"} <= clause.parameters.keys()
    ]
    if not rules:
        return None
    document, clause = rules[0]
    terms = _read(script, document, clause)
    if terms is None:
        return None

    script.hypothesis("H3 · The reference index then and now")
    series = next(s for s in state.proof.reference_values if s.name == terms["index"])
    latest = max(v.period for v in series.values if v.period <= f"{state.as_of:%Y-%m}")
    index = script.call(
        "get_reference_index",
        {"name": terms["index"], "periods": [terms["base_period"], latest]},
    )
    if not index.ok or index.result["missing"]:
        return None
    script.cite(index.result["series_id"])
    values = {v["period"]: int(Decimal(v["value"])) for v in index.result["values"]}
    base_amount = item.expected_amount if item and item.expected_amount else None
    computed = script.call(
        "compute",
        {
            "formula_id": "vpi_threshold_indexation",
            "inputs": {
                "base_amount": str(base_amount or finding.impact.expected),
                "base_index": values[terms["base_period"]],
                "current_index": values[latest],
                "threshold_bps": terms["threshold_bps"],
            },
        },
    )
    return computed.call_id if computed.ok else None


def _new_charge(script: _Script, finding: ProofFinding) -> str | None:
    """A new charge on an invoice: its announcement, the announced terms, the rule."""
    charges = [
        d for d in finding.differences if d.kind is DifferenceKind.NEW_CHARGE and not d.explained
    ]
    announced = _letter(script, finding, " ".join(d.description for d in charges))

    script.hypothesis("H2 · A clause in what was announced")
    rules = [
        (document, clause)
        for document in announced
        for clause in document.clauses
        if {"surcharge", "below_net_goods", "valid_from"} <= clause.parameters.keys()
    ]
    if not rules:
        return None
    document, clause = rules[0]
    terms = _read(script, document, clause)
    if terms is None or finding.impact.expected is None or finding.period_from is None:
        return None

    script.hypothesis("H3 · Whether the rule applies to this delivery")
    computed = script.call(
        "compute",
        {
            "formula_id": "surcharge_rule",
            "inputs": {
                "net_goods": str(finding.impact.expected),
                "date": finding.period_from.isoformat(),
                "surcharge": terms["surcharge"],
                "below_net_goods": terms["below_net_goods"],
                "valid_from": terms["valid_from"],
            },
        },
    )
    return computed.call_id if computed.ok else None


@dataclass(frozen=True, slots=True)
class ScriptedResult:
    grounding: Grounding
    steps: tuple[tuple[str, str], ...]


def investigate(state: DemoState, finding: ProofFinding) -> ScriptedResult:
    """Run the hypotheses for this finding and ground the result like an agent's answer."""
    script = _Script(state)
    compute_call_id = None
    if finding.kind is FindingKind.RECURRING_AMOUNT:
        compute_call_id = _recurring(script, finding)
    elif any(d.kind is DifferenceKind.NEW_CHARGE and not d.explained for d in finding.differences):
        compute_call_id = _new_charge(script, finding)
    documents = {d.id: d for d in state.proof.documents} | {
        u.document_id: u.document for u in state.uploads.values() if u.document
    }
    item = next((c for c in state.cost_memory if c.cost_ref == finding.cost_ref), None)
    grounding = ground(
        finding,
        {"compute_call_id": compute_call_id, "evidence_refs": script.cited},
        script.calls,
        base_sources(finding, item, documents.get(finding.document_id or "")),
        state.proof,
        documents,
        state.forecast_input(),
        mode=InvestigationMode.FALLBACK,
    )
    steps = (*script.steps, *(("GROUND", step) for step in grounding.steps))
    return ScriptedResult(grounding, steps)


def investigate_preloaded(state: DemoState, at: datetime) -> dict:
    """At load and reset: the scripted investigation of every finding CHECK left unexplained,
    with the load time, so every reset gives the same findings."""
    findings = dict(state.findings)
    for finding_id, finding in state.findings.items():
        if (
            finding.classification is not Classification.UNEXPLAINED
            or finding.status is not FindingStatus.CHECKED
        ):
            continue
        investigating = finding.advance(at, FindingStatus.INVESTIGATING, "Looking for the reason")
        result = investigate(state, investigating)
        findings[finding_id] = result.grounding.apply(investigating, at)
    return findings
