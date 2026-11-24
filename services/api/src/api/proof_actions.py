"""Vero Proof's actions on a classified finding (docs/PROOF.md §5.8, §6): keep paying the
amount the contract allows, change a recurring amount, or ask the counterparty in writing.

Every action goes through the policy and waits for the owner (`NEEDS_APPROVAL`), or is refused
(`NOT_ALLOWED`); approving and rejecting go through `decisions.decide`, whose executor is the
only code that changes the schedule."""

from datetime import date, datetime
from uuid import uuid4

from api.audit import AuditActor, AuditAgent, record
from api.decisions import (
    _checks,
    _first_failure,
    _receipt,
    _store,
    _summary_with,
    impact,
    subject_text,
)
from api.domain.models import (
    Category,
    Outflow,
    Recommendation,
    RecommendationKind,
    RecommendationStatus,
    Run,
)
from api.domain.policy import Lane, evaluate_proof_action
from api.domain.proof.findings import (
    Classification,
    FindingStatus,
    PaymentDecision,
    ProofFinding,
)
from api.letters import cited_clause, contract_word, write_letter
from api.money import format_eur
from api.state import DemoState


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _payments(state: DemoState, finding: ProofFinding) -> list[Outflow]:
    refs = finding.propagation.forecast.payments if finding.propagation.forecast else ()
    return [o for o in state.outflows if o.ref in refs]


def _clause(state: DemoState, finding: ProofFinding) -> tuple[str, str] | None:
    """The section and contract word of the first clause the finding cites."""
    cited = cited_clause(state, finding)
    return (cited[1].section, contract_word(cited[0])) if cited else None


def letter_subject(state: DemoState, finding: ProofFinding, category: Category | None) -> str:
    """German subject line, neutral, citing the clause (PROOF §7.1)."""
    clause = _clause(state, finding)
    basis = f" gemäß {clause[0]} {clause[1]}" if clause else ""
    start = finding.period_from
    if category is Category.RENT and start:
        return f"Ihre Mietanpassung ab {start:%d.%m.%Y} – Bitte um Erläuterung{basis}"
    document = state.proof.document(finding.document_id) if finding.document_id else None
    if document and document.document_number:
        return f"Ihre Rechnung {document.document_number} – Bitte um Erläuterung{basis}"
    when = f" ab {start:%d.%m.%Y}" if start else ""
    return f"Ihre Preisanpassung{when} – Bitte um Erläuterung{basis}"


def _new(
    state: DemoState,
    run: Run,
    finding: ProofFinding,
    kind: RecommendationKind,
    category: Category | None,
    subject: dict,
    text: str,
    now: datetime,
    after: dict,
    outflows: list[Outflow] = (),
    amount: int | None = None,
) -> Recommendation:
    decision = evaluate_proof_action(kind, category, finding.counterparty, state.active_mandate)
    if decision.lane is Lane.NOT_ALLOWED:
        text = f"{text} Not allowed by your rules: {_first_failure(decision)}."
    before = impact(state.forecast().summary)
    return Recommendation(
        id=uuid4(),
        run_id=run.id,
        kind=kind,
        lane=decision.lane.value,
        status=RecommendationStatus.BLOCKED
        if decision.lane is Lane.NOT_ALLOWED
        else RecommendationStatus.PENDING,
        created_at=now,
        based_on_financial_version=state.financial_version,
        details={
            "subject": {"kind": kind.value, "finding_id": str(finding.id), **subject},
            "reason": {
                "first_breach": None,
                "shortfall": 0,
                "finding_id": str(finding.id),
                "disputed": finding.propagation.disputed,
            },
            "checks": _checks(decision),
            "impact": {"before": before, "after_if_executed": after},
            "explanation": {"text": text, "source": "TEMPLATE"},
        },
        decided_at=now if decision.lane is Lane.NOT_ALLOWED else None,
        finding_id=finding.id,
        outflow_ids=tuple(o.id for o in outflows),
        outflow_versions=tuple(o.version for o in outflows),
        amount=amount,
    )


def propose_actions(
    state: DemoState,
    run: Run,
    finding: ProofFinding,
    now: datetime,
    trace,
    proposer: AuditActor = AuditActor.SYSTEM,
    model: str | None = None,
    letter_draft: str | None = None,
) -> ProofFinding:
    """The actions a classified finding calls for: a conflict keeps the allowed amount (or
    changes to it) and asks in writing; a verified new amount changes the recurring payment.
    The finding then waits for the owner (PENDING_APPROVAL)."""
    p = finding.propagation
    if p is None or p.decision is PaymentDecision.PAID and not p.disputed:
        return finding
    item = next((c for c in state.cost_memory if c.cost_ref == finding.cost_ref), None)
    category = item.category if item else None
    payments = _payments(state, finding)
    listed = [
        {"outflow_id": str(o.id), "ref": o.ref, "date": o.scheduled_date.isoformat()}
        for o in payments
    ]
    unchanged = impact(state.forecast().summary)
    conflict = finding.classification is Classification.CONFLICT
    created: list[Recommendation] = []

    if conflict and p.decision is PaymentDecision.KEEP:
        text = (
            f"Keep paying {format_eur(p.current_amount)} for {p.cost_ref}, the amount the "
            f"contract allows: {format_eur(p.billed_amount)} is billed, "
            f"{format_eur(p.disputed)} above it."
        )
        if category is Category.RENT:
            text += " Never withhold rent: pay the contractual amount and ask in writing."
        created.append(
            _new(
                state,
                run,
                finding,
                RecommendationKind.KEEP_PAYMENT,
                category,
                {
                    "cost_ref": p.cost_ref,
                    "counterparty": finding.counterparty,
                    "category": category.value if category else None,
                    "amount": p.current_amount,
                    "billed_amount": p.billed_amount,
                    "effective_from": p.effective_from.isoformat() if p.effective_from else None,
                    "payments": listed,
                },
                text,
                now,
                unchanged,
            )
        )
    if p.decision is PaymentDecision.UPDATE and payments:
        new_amounts = [(o.id, p.proposed_amount) for o in payments]
        created.append(
            _new(
                state,
                run,
                finding,
                RecommendationKind.UPDATE_RECURRING_AMOUNT,
                category,
                {
                    "cost_ref": p.cost_ref,
                    "counterparty": finding.counterparty,
                    "category": category.value if category else None,
                    "from_amount": p.current_amount,
                    "to_amount": p.proposed_amount,
                    "effective_from": p.effective_from.isoformat(),
                    "payments": listed,
                },
                f"Pay {format_eur(p.proposed_amount)} instead of "
                f"{format_eur(p.current_amount)} for {p.cost_ref} from "
                f"{_day(p.effective_from)}: {len(payments)} payment"
                f"{'s' if len(payments) != 1 else ''} in the next 13 weeks change.",
                now,
                impact(_summary_with(state, amounts=new_amounts)),
                payments,
                p.proposed_amount,
            )
        )
    if conflict:
        clause = _clause(state, finding)
        basis = f"what {clause[0]} allows" if clause else "what was agreed"
        letter = write_letter(state, finding, category, letter_draft)
        if letter.rejected:
            trace.add("GROUND", f"The agent's letter is not used: {letter.rejected}", now)
        created.append(
            _new(
                state,
                run,
                finding,
                RecommendationKind.SEND_LETTER,
                category,
                {
                    "counterparty": finding.counterparty,
                    "counterparty_id": finding.counterparty_id,
                    "letter_subject": letter_subject(state, finding, category),
                    "language": "de",
                    "draft": letter.text,
                    "draft_source": letter.source,
                },
                f"Ask {finding.counterparty} in writing for the basis of the change: "
                f"{format_eur(p.disputed)} above {basis}.",
                now,
                unchanged,
            )
        )
    if not created:
        return finding

    for rec in created:
        receipt = _receipt(
            state,
            rec,
            "PROPOSAL",
            now,
            run.trigger_label,
            rec.details["impact"]["before"],
            rec.details["impact"]["after_if_executed"],
        )
        rec = _store(state, rec, receipt)
        lane = Lane(rec.lane)
        what = subject_text(rec)
        trace.add(
            "POLICY",
            f"{what[0].upper()}{what[1:]}: {lane.value.replace('_', ' ').lower()}",
            now,
        )
        common = {
            "agent": AuditAgent.PROOF,
            "run_id": run.id,
            "recommendation_id": rec.id,
            "lane": rec.lane,
        }
        record(state, now, proposer, f"Proposed: {subject_text(rec)}", model=model, **common)
        if rec.status is RecommendationStatus.BLOCKED:
            record(
                state,
                now,
                AuditActor.RULES,
                f"Not allowed by your rules: {rec.details['explanation']['text']}",
                **common,
            )
    ids = tuple(r.id for r in created)
    pending = sum(1 for r in created if r.status is RecommendationStatus.PENDING)
    if not pending:
        return finding.model_copy(update={"recommendation_ids": ids})
    return finding.advance(
        now,
        FindingStatus.PENDING_APPROVAL,
        f"Waiting for your decision on {pending} action{'s' if pending != 1 else ''}",
        recommendation_ids=ids,
    )
