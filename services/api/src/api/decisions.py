"""Runs, recommendations, simulated execution and receipts. Callers hold the write lock.

The executor functions here are the only code that changes a payment date or adds a credit
line. Every executed action is simulated: nothing reaches a real bank."""

from dataclasses import replace
from datetime import date, datetime
from uuid import UUID, uuid4

from api.domain.credit import size_credit_line
from api.domain.forecast import ENGINE_VERSION, ForecastSummary, compute_forecast
from api.domain.models import (
    CreditLine,
    Outflow,
    Receipt,
    Recommendation,
    RecommendationKind,
    RecommendationStatus,
    Run,
    RunMode,
    RunStatus,
    TraceStep,
)
from api.domain.planner import PLANNER_VERSION, apply_moves
from api.domain.policy import (
    POLICY_VERSION,
    Lane,
    PolicyDecision,
    evaluate_credit,
    evaluate_move,
)
from api.domain.revenue import MODEL_VERSION
from api.money import Cents, format_eur
from api.state import DemoState

CREDIT_PRODUCT = "SHORT_TERM_CREDIT_LINE"


class Tracer:
    def __init__(self, run: Run) -> None:
        self.steps = list(run.trace)

    def add(self, kind: str, label: str, now: datetime, tool: str | None = None, data=None) -> None:
        self.steps.append(TraceStep(len(self.steps) + 1, kind, "BACKEND", label, now, tool, data))


def _day(d: date) -> str:
    return f"{d.day} {d:%b}"


def impact(summary: ForecastSummary) -> dict:
    return {
        "first_breach": summary.first_breach.date.isoformat() if summary.first_breach else None,
        "lowest": summary.lowest.closing,
        "lowest_date": summary.lowest.date.isoformat(),
        "days_below_floor": summary.days_below_floor,
    }


def _checks(decision: PolicyDecision) -> list[dict]:
    return [
        {"rule": c.rule, "result": c.result.value, "effect": c.effect, "text": c.text}
        for c in decision.checks
    ]


def _first_failure(decision: PolicyDecision) -> str:
    strongest = sorted(decision.failed, key=lambda c: 0 if c.effect is Lane.NOT_ALLOWED else 1)
    return strongest[0].text if strongest else ""


def _confirmation() -> str:
    return f"SIM-{uuid4().hex[:8].upper()}"


def _versions() -> dict[str, str]:
    return {
        "engine": ENGINE_VERSION,
        "planner": PLANNER_VERSION,
        "policy": POLICY_VERSION,
        "revenue_model": MODEL_VERSION,
    }


def _summary_with(
    state: DemoState, moves=(), lines: tuple[CreditLine, ...] = ()
) -> ForecastSummary:
    data = state.forecast_input()
    data = apply_moves(data, list(moves))
    data = replace(data, credit_lines=(*data.credit_lines, *lines))
    return compute_forecast(data).summary


def execute_move(state: DemoState, outflow_id: UUID, to_date: date) -> Outflow:
    outflow = next(o for o in state.outflows if o.id == outflow_id)
    moved = replace(outflow, scheduled_date=to_date, version=outflow.version + 1)
    state.outflows = [moved if o.id == outflow_id else o for o in state.outflows]
    state.financial_version += 1
    return moved


def execute_credit(state: DemoState, line: CreditLine) -> None:
    state.credit_lines.append(line)
    state.financial_version += 1


def _move_subject(outflow: Outflow, to_date: date) -> dict:
    return {
        "kind": RecommendationKind.RESCHEDULE_PAYMENT.value,
        "outflow_id": str(outflow.id),
        "ref": outflow.ref,
        "counterparty": outflow.counterparty,
        "category": outflow.category.value,
        "amount": outflow.amount,
        "original_amount": outflow.original_amount,
        "original_currency": outflow.original_currency,
        "from_date": outflow.scheduled_date.isoformat(),
        "to_date": to_date.isoformat(),
    }


def _move_text(lane: Lane, outflow: Outflow, to_date: date, decision: PolicyDecision) -> str:
    what = f"{outflow.counterparty} ({format_eur(outflow.amount)})"
    if lane is Lane.DONE_WITHIN_MANDATE:
        return (
            f"Postponed {what} from {_day(outflow.scheduled_date)} to {_day(to_date)}, "
            "within your rules (simulated)."
        )
    if lane is Lane.NEEDS_APPROVAL:
        return (
            f"Postponing {what} to {_day(to_date)} needs your approval: {_first_failure(decision)}."
        )
    return f"{what} cannot be postponed: {_first_failure(decision)}."


def _credit_text(lane: Lane, line: CreditLine, shortfall: Cents, decision: PolicyDecision) -> str:
    if lane is Lane.NOT_ALLOWED:
        return (
            f"A credit line of {format_eur(line.amount)} would cover the gap, but "
            f"{_first_failure(decision)[0].lower()}{_first_failure(decision)[1:]}."
        )
    return (
        f"A short-term credit line of {format_eur(line.amount)} from {_day(line.available_from)} "
        f"would cover the remaining gap of {format_eur(shortfall)} "
        "(illustrative, not a bank offer)."
    )


def _receipt(
    state: DemoState,
    rec: Recommendation,
    kind: str,
    now: datetime,
    trigger: str,
    before: dict,
    after: dict,
    decision: str | None = None,
) -> Receipt:
    receipt = Receipt(
        id=uuid4(),
        recommendation_id=rec.id,
        kind=kind,
        created_at=now,
        body={
            "trigger": trigger,
            "subject": rec.details["subject"],
            "lane": rec.lane,
            "decision": decision,
            "checks": rec.details["checks"],
            "before": before,
            "after": after,
            "assumptions": [a.code for a in state.forecast().assumptions],
            "mandate_version": state.active_mandate.version,
            "financial_version": state.financial_version,
            "versions": _versions(),
            "execution": "SIMULATED",
            "explanation": rec.details["explanation"],
        },
    )
    state.receipts[receipt.id] = receipt
    return receipt


def _store(state: DemoState, rec: Recommendation, receipt: Receipt) -> Recommendation:
    rec = replace(rec, receipt_ids=(*rec.receipt_ids, receipt.id))
    state.recommendations[rec.id] = rec
    return rec


def _supersede_pending(state: DemoState, now: datetime) -> None:
    for rec_id, rec in list(state.recommendations.items()):
        if rec.status is RecommendationStatus.PENDING:
            state.recommendations[rec_id] = replace(
                rec, status=RecommendationStatus.SUPERSEDED, decided_at=now
            )


def _moved_alone_today(state: DemoState) -> Cents:
    return sum(
        r.details["subject"]["amount"]
        for r in state.recommendations.values()
        if r.kind is RecommendationKind.RESCHEDULE_PAYMENT
        and r.status is RecommendationStatus.EXECUTED
    )


def headline(
    trigger: str, floor: Cents, before: ForecastSummary, recs: list[Recommendation]
) -> str:
    if before.first_breach is None:
        return f"{trigger}: your cash stays above your {format_eur(floor)} floor for 13 weeks."
    done = [r for r in recs if r.status is RecommendationStatus.EXECUTED]
    pending = [r for r in recs if r.status is RecommendationStatus.PENDING]
    parts = [
        f"{trigger}: your cash would fall below your {format_eur(floor)} floor from "
        f"{_day(before.first_breach.date)}."
    ]
    if done:
        parts.append(f"I did {len(done)} thing{'s' if len(done) > 1 else ''} within your rules.")
    if pending:
        parts.append(f"{len(pending)} need{'s' if len(pending) == 1 else ''} your approval.")
    return " ".join(parts)


def run_fallback(
    state: DemoState, run: Run, now: datetime, credit_until: date, credit_rounding: Cents
) -> Run:
    """Backend-only pipeline: forecast, plan, check, act within the mandate, size credit."""
    trace = Tracer(run)
    mandate = state.active_mandate
    trace.add("TOOL_CALL", "Recomputing your cash for the next 91 days", now, "get_forecast")
    before = state.forecast().summary
    if before.first_breach is None:
        trace.add("TOOL_RESULT", "Your cash stays above your floor for the next 13 weeks", now)
        return _finish(state, run, trace, before, [], now)

    trace.add(
        "TOOL_RESULT",
        f"Below your {format_eur(mandate.cash_floor)} floor from {_day(before.first_breach.date)}, "
        f"lowest {format_eur(before.lowest.closing)} on {_day(before.lowest.date)}",
        now,
    )
    _supersede_pending(state, now)

    trace.add("TOOL_CALL", "Looking for payments that can wait", now, "find_reschedule_options")
    plan = state.plan()
    trace.add(
        "TOOL_RESULT",
        f"Found {len(plan.candidates)} payment(s) worth postponing"
        if plan.candidates
        else "No payment can usefully wait",
        now,
    )

    by_id = {o.id: o for o in state.outflows}
    created: list[Recommendation] = []
    for candidate in plan.candidates:
        outflow = by_id[candidate.outflow_id]
        decision = evaluate_move(
            outflow, candidate.to_date, mandate, state.as_of, _moved_alone_today(state)
        )
        before_move = state.forecast().summary
        after_move = _summary_with(state, [candidate])
        lane = decision.lane
        rec = Recommendation(
            id=uuid4(),
            run_id=run.id,
            kind=RecommendationKind.RESCHEDULE_PAYMENT,
            lane=lane.value,
            status=RecommendationStatus.PENDING,
            created_at=now,
            based_on_financial_version=state.financial_version,
            details={
                "subject": _move_subject(outflow, candidate.to_date),
                "reason": {
                    "first_breach": before_move.first_breach.date.isoformat()
                    if before_move.first_breach
                    else None,
                    "shortfall": before_move.first_breach.shortfall
                    if before_move.first_breach
                    else 0,
                },
                "checks": _checks(decision),
                "impact": {"before": impact(before_move), "after_if_executed": impact(after_move)},
                "explanation": {
                    "text": _move_text(lane, outflow, candidate.to_date, decision),
                    "source": "TEMPLATE",
                },
            },
            outflow_id=outflow.id,
            outflow_version=outflow.version,
            to_date=candidate.to_date,
        )
        trace.add("POLICY", f"{outflow.counterparty}: {lane.value.replace('_', ' ').lower()}", now)
        if lane is Lane.DONE_WITHIN_MANDATE:
            execute_move(state, outflow.id, candidate.to_date)
            rec = replace(
                rec,
                status=RecommendationStatus.EXECUTED,
                executed_by="VERO",
                executed_at=now,
                confirmation_ref=_confirmation(),
                decided_at=now,
            )
            trace.add(
                "EXECUTE",
                f"Postponed {outflow.counterparty} to {_day(candidate.to_date)} (simulated)",
                now,
            )
        elif lane is Lane.NOT_ALLOWED:
            rec = replace(rec, status=RecommendationStatus.BLOCKED, decided_at=now)
        rec = _store(
            state,
            rec,
            _receipt(
                state,
                rec,
                "PROPOSAL",
                now,
                run.trigger_label,
                impact(before_move),
                impact(after_move),
            ),
        )
        created.append(rec)

    trace.add("TOOL_CALL", "Sizing a credit line for what's left", now, "size_credit_line")
    sizing = size_credit_line(state.forecast_input(), credit_until, credit_rounding)
    if sizing.needed and sizing.credit_line is not None:
        line = sizing.credit_line
        decision = evaluate_credit(line, mandate)
        current = state.forecast().summary
        rec = Recommendation(
            id=uuid4(),
            run_id=run.id,
            kind=RecommendationKind.CREDIT_LINE,
            lane=decision.lane.value,
            status=RecommendationStatus.BLOCKED
            if decision.lane is Lane.NOT_ALLOWED
            else RecommendationStatus.PENDING,
            created_at=now,
            based_on_financial_version=state.financial_version,
            details={
                "subject": {
                    "kind": RecommendationKind.CREDIT_LINE.value,
                    "product": CREDIT_PRODUCT,
                    "amount": line.amount,
                    "available_from": line.available_from.isoformat(),
                    "available_until": line.available_until.isoformat(),
                    "shortfall": sizing.shortfall,
                    "rounded_to": sizing.rounded_to,
                    "headroom_at_lowest": sizing.headroom_at_lowest,
                    "illustrative": True,
                },
                "reason": {
                    "first_breach": current.first_breach.date.isoformat()
                    if current.first_breach
                    else None,
                    "shortfall": sizing.shortfall,
                },
                "checks": _checks(decision),
                "impact": {
                    "before": impact(current),
                    "after_if_executed": impact(_summary_with(state, lines=(line,))),
                },
                "explanation": {
                    "text": _credit_text(decision.lane, line, sizing.shortfall, decision),
                    "source": "TEMPLATE",
                },
            },
            credit_line=line,
            decided_at=now if decision.lane is Lane.NOT_ALLOWED else None,
        )
        trace.add(
            "POLICY",
            f"Credit line of {format_eur(line.amount)}: "
            f"{decision.lane.value.replace('_', ' ').lower()}",
            now,
        )
        rec = _store(
            state,
            rec,
            _receipt(
                state,
                rec,
                "PROPOSAL",
                now,
                run.trigger_label,
                rec.details["impact"]["before"],
                rec.details["impact"]["after_if_executed"],
            ),
        )
        created.append(rec)
    else:
        trace.add("TOOL_RESULT", "No credit needed", now)

    for rec in created:
        if rec.status is RecommendationStatus.PENDING:
            state.recommendations[rec.id] = replace(
                rec, based_on_financial_version=state.financial_version
            )
    created = [state.recommendations[r.id] for r in created]
    return _finish(state, run, trace, before, created, now)


def _finish(
    state: DemoState,
    run: Run,
    trace: Tracer,
    before: ForecastSummary,
    recs: list[Recommendation],
    now: datetime,
) -> Run:
    after = state.forecast().summary
    text = headline(run.trigger_label, state.active_mandate.cash_floor, before, recs)
    trace.add("SUMMARY", text, now)
    finished = replace(
        run,
        status=RunStatus.SUCCEEDED,
        mode=RunMode.FALLBACK,
        finished_at=now,
        forecast_id=state.forecast_id,
        trace=tuple(trace.steps),
        recommendation_ids=tuple(r.id for r in recs),
        headline=text,
        gap_closed=after.first_breach is None,
    )
    state.runs[run.id] = finished
    return finished


class DecisionError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _is_stale(state: DemoState, rec: Recommendation) -> bool:
    if state.financial_version != rec.based_on_financial_version:
        return True
    if rec.outflow_id is not None:
        outflow = next((o for o in state.outflows if o.id == rec.outflow_id), None)
        return outflow is None or outflow.version != rec.outflow_version
    return False


def decide(
    state: DemoState, rec_id: UUID, approve: bool, note: str | None, now: datetime
) -> tuple[Recommendation, Receipt]:
    """Approve or reject a pending recommendation. Raises DecisionError for NOT_FOUND,
    NOT_PENDING and STALE_RECOMMENDATION; a stale recommendation is marked STALE first."""
    rec = state.recommendations.get(rec_id)
    if rec is None:
        raise DecisionError("NOT_FOUND", f"no recommendation {rec_id}")
    if rec.status is not RecommendationStatus.PENDING:
        raise DecisionError("NOT_PENDING", f"recommendation is {rec.status}")
    if _is_stale(state, rec):
        state.recommendations[rec_id] = replace(
            rec, status=RecommendationStatus.STALE, decided_at=now
        )
        raise DecisionError(
            "STALE_RECOMMENDATION", "the data changed since this was proposed; plan again"
        )

    before = impact(state.forecast().summary)
    if not approve:
        rec = replace(rec, status=RecommendationStatus.REJECTED, decided_at=now, note=note)
        receipt = _receipt(state, rec, "DECISION", now, "Your decision", before, before, "REJECTED")
        return _store(state, rec, receipt), receipt

    mandate = state.active_mandate
    if rec.kind is RecommendationKind.RESCHEDULE_PAYMENT:
        outflow = next(o for o in state.outflows if o.id == rec.outflow_id)
        decision = evaluate_move(outflow, rec.to_date, mandate, state.as_of, owner_approved=True)
    else:
        decision = evaluate_credit(rec.credit_line, mandate, owner_approved=True)
    details = rec.details | {"checks": _checks(decision)}

    if decision.lane is Lane.NOT_ALLOWED:
        rec = replace(
            rec, status=RecommendationStatus.BLOCKED, decided_at=now, note=note, details=details
        )
        receipt = _receipt(state, rec, "DECISION", now, "Your decision", before, before, "APPROVED")
        return _store(state, rec, receipt), receipt

    if rec.kind is RecommendationKind.RESCHEDULE_PAYMENT:
        execute_move(state, rec.outflow_id, rec.to_date)
    else:
        execute_credit(state, rec.credit_line)
    rec = replace(
        rec,
        status=RecommendationStatus.APPROVED_EXECUTED,
        decided_at=now,
        executed_at=now,
        executed_by="OWNER",
        confirmation_ref=_confirmation(),
        note=note,
        details=details,
    )
    after = impact(state.forecast().summary)
    receipt = _receipt(state, rec, "DECISION", now, "Your decision", before, after, "APPROVED")
    return _store(state, rec, receipt), receipt


def request_reschedule(
    state: DemoState, run: Run, outflow: Outflow, to_date: date, now: datetime
) -> Run:
    """The owner asks to move a payment: same policy, with the owner's approval implied."""
    trace = Tracer(run)
    before_summary = state.forecast().summary
    decision = evaluate_move(
        outflow, to_date, state.active_mandate, state.as_of, owner_approved=True
    )
    lane = decision.lane
    trace.add("POLICY", f"{outflow.counterparty}: {lane.value.replace('_', ' ').lower()}", now)
    before = impact(before_summary)
    rec = Recommendation(
        id=uuid4(),
        run_id=run.id,
        kind=RecommendationKind.RESCHEDULE_PAYMENT,
        lane=lane.value,
        status=RecommendationStatus.BLOCKED,
        created_at=now,
        based_on_financial_version=state.financial_version,
        details={
            "subject": _move_subject(outflow, to_date),
            "reason": {"first_breach": None, "shortfall": 0, "requested_by": "OWNER"},
            "checks": _checks(decision),
            "impact": {"before": before, "after_if_executed": before},
            "explanation": {
                "text": _move_text(lane, outflow, to_date, decision)
                if lane is Lane.NOT_ALLOWED
                else f"Postponed {outflow.counterparty} to {_day(to_date)} as asked (simulated).",
                "source": "TEMPLATE",
            },
        },
        outflow_id=outflow.id,
        outflow_version=outflow.version,
        to_date=to_date,
        decided_at=now,
    )
    if lane is not Lane.NOT_ALLOWED:
        execute_move(state, outflow.id, to_date)
        after = impact(state.forecast().summary)
        rec = replace(
            rec,
            status=RecommendationStatus.APPROVED_EXECUTED,
            executed_by="OWNER",
            executed_at=now,
            confirmation_ref=_confirmation(),
            details=rec.details | {"impact": {"before": before, "after_if_executed": after}},
        )
        trace.add(
            "EXECUTE", f"Postponed {outflow.counterparty} to {_day(to_date)} (simulated)", now
        )
    rec = _store(
        state,
        rec,
        _receipt(
            state,
            rec,
            "DECISION",
            now,
            run.trigger_label,
            before,
            rec.details["impact"]["after_if_executed"],
            "REQUESTED",
        ),
    )
    return _finish(state, run, trace, before_summary, [rec], now)
