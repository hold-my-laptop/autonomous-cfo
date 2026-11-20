"""Runs, recommendations, simulated execution and receipts. Callers hold the write lock.

The executor functions here are the only code that changes a payment date or adds a credit
line. Every executed action is simulated: nothing reaches a real bank."""

from dataclasses import dataclass, replace
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
from api.domain.planner import PLANNER_VERSION
from api.domain.policy import (
    POLICY_VERSION,
    Lane,
    PolicyDecision,
    evaluate_credit,
    evaluate_move,
)
from api.domain.revenue import MODEL_VERSION
from api.explain import explain_request
from api.money import Cents, format_eur
from api.state import DemoState

CREDIT_PRODUCT = "SHORT_TERM_CREDIT_LINE"


class Tracer:
    def __init__(self, run: Run) -> None:
        self.steps = list(run.trace)

    def add(self, kind: str, label: str, now: datetime, tool: str | None = None, data=None) -> None:
        self.steps.append(TraceStep(len(self.steps) + 1, kind, "BACKEND", label, now, tool, data))


def _lane_text(lane: Lane) -> str:
    return lane.value.replace("_", " ").lower()


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
    targets = dict(moves)
    data = replace(
        data,
        outflows=tuple(
            replace(o, scheduled_date=targets[o.id]) if o.id in targets else o
            for o in data.outflows
        ),
        credit_lines=(*data.credit_lines, *lines),
    )
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


def moved_alone_today(state: DemoState) -> Cents:
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


@dataclass(frozen=True)
class CreditProposal:
    line: CreditLine
    shortfall: Cents
    rounded_to: Cents
    headroom_at_lowest: Cents


def _propose_move(
    state: DemoState, run: Run, trace: Tracer, outflow: Outflow, to_date: date, now: datetime
) -> Recommendation:
    mandate = state.active_mandate
    decision = evaluate_move(
        outflow,
        to_date,
        mandate,
        state.as_of,
        moved_alone_today(state),
        window_end=state.window_end,
    )
    before_move = state.forecast().summary
    after_move = _summary_with(state, moves=[(outflow.id, to_date)])
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
            "subject": _move_subject(outflow, to_date),
            "reason": {
                "first_breach": before_move.first_breach.date.isoformat()
                if before_move.first_breach
                else None,
                "shortfall": before_move.first_breach.shortfall if before_move.first_breach else 0,
            },
            "checks": _checks(decision),
            "impact": {"before": impact(before_move), "after_if_executed": impact(after_move)},
            "explanation": {
                "text": _move_text(lane, outflow, to_date, decision),
                "source": "TEMPLATE",
            },
        },
        outflow_id=outflow.id,
        outflow_version=outflow.version,
        to_date=to_date,
    )
    trace.add("POLICY", f"{outflow.counterparty}: {_lane_text(lane)}", now)
    if lane is Lane.DONE_WITHIN_MANDATE:
        execute_move(state, outflow.id, to_date)
        rec = replace(
            rec,
            status=RecommendationStatus.EXECUTED,
            executed_by="VERO",
            executed_at=now,
            confirmation_ref=_confirmation(),
            decided_at=now,
        )
        trace.add(
            "EXECUTE", f"Postponed {outflow.counterparty} to {_day(to_date)} (simulated)", now
        )
    elif lane is Lane.NOT_ALLOWED:
        rec = replace(rec, status=RecommendationStatus.BLOCKED, decided_at=now)
    receipt = _receipt(
        state, rec, "PROPOSAL", now, run.trigger_label, impact(before_move), impact(after_move)
    )
    return _store(state, rec, receipt)


def _propose_credit(
    state: DemoState, run: Run, trace: Tracer, credit: CreditProposal, now: datetime
) -> Recommendation:
    line = credit.line
    decision = evaluate_credit(line, state.active_mandate)
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
                "shortfall": credit.shortfall,
                "rounded_to": credit.rounded_to,
                "headroom_at_lowest": credit.headroom_at_lowest,
                "illustrative": True,
            },
            "reason": {
                "first_breach": current.first_breach.date.isoformat()
                if current.first_breach
                else None,
                "shortfall": credit.shortfall,
            },
            "checks": _checks(decision),
            "impact": {
                "before": impact(current),
                "after_if_executed": impact(_summary_with(state, lines=(line,))),
            },
            "explanation": {
                "text": _credit_text(decision.lane, line, credit.shortfall, decision),
                "source": "TEMPLATE",
            },
        },
        credit_line=line,
        decided_at=now if decision.lane is Lane.NOT_ALLOWED else None,
    )
    trace.add(
        "POLICY",
        f"Credit line of {format_eur(line.amount)}: {_lane_text(decision.lane)}",
        now,
    )
    receipt = _receipt(
        state,
        rec,
        "PROPOSAL",
        now,
        run.trigger_label,
        rec.details["impact"]["before"],
        rec.details["impact"]["after_if_executed"],
    )
    return _store(state, rec, receipt)


def _pin_pending(state: DemoState, created: list[Recommendation]) -> list[Recommendation]:
    """Pending proposals are valid against the data as it is once the run has acted."""
    for rec in created:
        if rec.status is RecommendationStatus.PENDING:
            state.recommendations[rec.id] = replace(
                rec, based_on_financial_version=state.financial_version
            )
    return [state.recommendations[r.id] for r in created]


def _credit_now(
    state: DemoState, credit_until: date, credit_rounding: Cents
) -> CreditProposal | None:
    """The credit line the current data needs, after whatever has been executed so far."""
    sizing = size_credit_line(state.forecast_input(), credit_until, credit_rounding)
    if not sizing.needed or sizing.credit_line is None:
        return None
    return CreditProposal(
        sizing.credit_line, sizing.shortfall, sizing.rounded_to, sizing.headroom_at_lowest
    )


def run_fallback(
    state: DemoState,
    run: Run,
    now: datetime,
    credit_until: date,
    credit_rounding: Cents,
    finalizing: bool = False,
) -> Run:
    """Backend-only pipeline: forecast, plan, check, act within the mandate, size credit.

    With finalizing the run waits in FINALIZING for the AI service's texts."""
    trace = Tracer(run)
    mandate = state.active_mandate
    trace.add("TOOL_CALL", "Recomputing your cash for the next 91 days", now, "get_forecast")
    before = state.forecast().summary
    if before.first_breach is None:
        trace.add("TOOL_RESULT", "Your cash stays above your floor for the next 13 weeks", now)
        return _finish(state, run, trace, before, [], now, RunMode.FALLBACK, finalizing=finalizing)

    trace.add(
        "TOOL_RESULT",
        f"Below your {format_eur(mandate.cash_floor)} floor from {_day(before.first_breach.date)}, "
        f"lowest {format_eur(before.lowest.closing)} on {_day(before.lowest.date)}",
        now,
    )
    _supersede_pending(state, now)

    trace.add("TOOL_CALL", "Looking for payments that can wait", now, "find_reschedule_options")
    plan = state.plan()
    count = len(plan.candidates)
    trace.add(
        "TOOL_RESULT",
        f"Found {count} payment{'s' if count != 1 else ''} worth postponing"
        if count
        else "No payment can usefully wait",
        now,
    )
    by_id = {o.id: o for o in state.outflows}
    created = [
        _propose_move(state, run, trace, by_id[c.outflow_id], c.to_date, now)
        for c in plan.candidates
    ]

    trace.add("TOOL_CALL", "Sizing a credit line for what's left", now, "size_credit_line")
    credit = _credit_now(state, credit_until, credit_rounding)
    if credit is not None:
        created.append(_propose_credit(state, run, trace, credit, now))
    else:
        trace.add("TOOL_RESULT", "No credit needed", now)

    return _finish(
        state,
        run,
        trace,
        before,
        _pin_pending(state, created),
        now,
        RunMode.FALLBACK,
        finalizing=finalizing,
    )


FALLBACK_DEADLINE = "DEADLINE_PASSED"
FALLBACK_AI_UNREACHABLE = "AI_UNREACHABLE"
FALLBACK_DATA_CHANGED = "DATA_CHANGED"
FALLBACK_RESTARTED = "SERVICE_RESTARTED"

_FALLBACK_LABELS = {
    FALLBACK_DEADLINE: "The model took too long",
    FALLBACK_AI_UNREACHABLE: "The AI service is unavailable",
    FALLBACK_DATA_CHANGED: "Your data changed during the analysis",
    FALLBACK_RESTARTED: "Vero restarted during the analysis",
}


def fallback_label(reason: str) -> str:
    """Plain words for why the backend finished the run; any other reason came from the
    agent giving up (e.g. MODEL_ERROR)."""
    cause = _FALLBACK_LABELS.get(reason, f"The model could not finish ({reason})")
    return f"{cause}, using standard checks"


def fall_back(
    state: DemoState,
    run: Run,
    reason: str,
    now: datetime,
    credit_until: date,
    credit_rounding: Cents,
) -> Run:
    trace = Tracer(run)
    trace.add("FALLBACK", fallback_label(reason), now, None, {"reason": reason})
    return run_fallback(
        state,
        replace(run, trace=tuple(trace.steps)),
        now,
        credit_until,
        credit_rounding,
        finalizing=True,
    )


def _resize_label(credit: CreditProposal, reason: str) -> str:
    line = credit.line
    return (
        f"Resized the credit line to {format_eur(line.amount)} from "
        f"{_day(line.available_from)} {reason}"
    )


def finalize_agent_run(
    state: DemoState,
    run: Run,
    proposals: list[dict],
    summary_note: str | None,
    agent_info: dict | None,
    now: datetime,
    credit_until: date,
    credit_rounding: Cents,
) -> Run:
    """Apply the agent's proposals through the same rules as the fallback.

    Moves must be candidates of this run's find_reschedule_options and the credit line must
    equal this run's size_credit_line result; anything else, and any repeat, is discarded and
    noted. Moves are applied first. The credit line is then sized again on the data after
    those moves, since a move waiting for approval has not happened."""
    trace = Tracer(run)
    log = run.tool_log or {}
    candidates = {tuple(c) for c in log.get("candidates", [])}
    credit_log = log.get("credit")
    if summary_note:
        trace.steps.append(
            TraceStep(len(trace.steps) + 1, "THINK", "AGENT", summary_note[:500], now)
        )
    before = state.forecast().summary
    _supersede_pending(state, now)
    created: list[Recommendation] = []
    moved: set[str] = set()
    credit_offered: dict | None = None
    for index, proposal in enumerate(proposals, start=1):
        kind = proposal.get("kind")
        if kind == RecommendationKind.RESCHEDULE_PAYMENT.value:
            move = proposal.get("move") or {}
            key = (str(move.get("outflow_id")), str(move.get("to_date")))
            if key[0] in moved:
                trace.add("DISCARDED", f"Proposal {index} ignored: payment already proposed", now)
                continue
            outflow = next((o for o in state.outflows if str(o.id) == key[0]), None)
            if key not in candidates or outflow is None:
                trace.add("DISCARDED", f"Proposal {index} ignored: not a planner candidate", now)
                continue
            moved.add(key[0])
            created.append(
                _propose_move(state, run, trace, outflow, date.fromisoformat(key[1]), now)
            )
        elif kind == RecommendationKind.CREDIT_LINE.value:
            line = proposal.get("credit_line") or {}
            offered = [str(line.get("amount")), str(line.get("available_from"))]
            if credit_offered is not None:
                trace.add(
                    "DISCARDED", f"Proposal {index} ignored: credit line already proposed", now
                )
            elif not credit_log or offered != [credit_log["amount"], credit_log["available_from"]]:
                trace.add("DISCARDED", f"Proposal {index} ignored: credit line was not sized", now)
            else:
                credit_offered = credit_log
        else:
            trace.add("DISCARDED", f"Proposal {index} ignored: unknown kind", now)

    if credit_offered is not None:
        credit = _credit_now(state, credit_until, credit_rounding)
        if credit is None:
            trace.add("DISCARDED", "Credit line dropped: no gap is left", now)
        else:
            if [str(credit.line.amount), credit.line.available_from.isoformat()] != [
                credit_offered["amount"],
                credit_offered["available_from"],
            ]:
                trace.add(
                    "TOOL_RESULT",
                    _resize_label(credit, "for the payments that still wait for you"),
                    now,
                    "size_credit_line",
                )
            created.append(_propose_credit(state, run, trace, credit, now))
    return _finish(
        state,
        run,
        trace,
        before,
        _pin_pending(state, created),
        now,
        RunMode.AGENT,
        agent_info,
        finalizing=True,
    )


def _finish(
    state: DemoState,
    run: Run,
    trace: Tracer,
    before: ForecastSummary,
    recs: list[Recommendation],
    now: datetime,
    mode: RunMode = RunMode.FALLBACK,
    summary_data: dict | None = None,
    finalizing: bool = False,
) -> Run:
    after = state.forecast().summary
    text = headline(run.trigger_label, state.active_mandate.cash_floor, before, recs)
    trace.add("SUMMARY", text, now, None, summary_data)
    finished = replace(
        run,
        status=RunStatus.FINALIZING if finalizing else RunStatus.SUCCEEDED,
        mode=mode,
        finished_at=now,
        forecast_id=state.forecast_id,
        trace=tuple(trace.steps),
        recommendation_ids=tuple(r.id for r in recs),
        headline=text,
        gap_closed=after.first_breach is None,
        explain_request=explain_request(state, run, before, recs) if finalizing else None,
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


def _refresh_pending(
    state: DemoState,
    decided: Recommendation,
    now: datetime,
    credit_until: date,
    credit_rounding: Cents,
) -> list[UUID]:
    """An approval changed the data; the other items of the same run still waiting for the
    owner are brought up to date instead of going stale.

    A move whose payment is unchanged keeps its lane with fresh impact numbers. A credit line
    is sized again: unchanged it is kept, otherwise it is superseded by the resized line (a
    follow-up) or dropped when no gap is left. Returns the follow-up ids."""
    follow_ups: list[UUID] = []
    current = state.forecast().summary
    for rec in list(state.recommendations.values()):
        if (
            rec.id == decided.id
            or rec.run_id != decided.run_id
            or rec.status is not RecommendationStatus.PENDING
        ):
            continue
        if rec.kind is RecommendationKind.RESCHEDULE_PAYMENT:
            outflow = next((o for o in state.outflows if o.id == rec.outflow_id), None)
            if outflow is None or outflow.version != rec.outflow_version:
                continue
            after = _summary_with(state, moves=[(outflow.id, rec.to_date)])
        else:
            credit = _credit_now(state, credit_until, credit_rounding)
            if credit is None or credit.line != rec.credit_line:
                state.recommendations[rec.id] = replace(
                    rec, status=RecommendationStatus.SUPERSEDED, decided_at=now
                )
                run = state.runs.get(rec.run_id)
                if run is None:
                    continue
                trace = Tracer(run)
                if credit is None:
                    trace.add(
                        "TOOL_RESULT", "Credit line no longer needed after your decision", now
                    )
                else:
                    trace.add(
                        "TOOL_RESULT",
                        _resize_label(credit, "after your decision"),
                        now,
                        "size_credit_line",
                    )
                    follow_ups.append(_propose_credit(state, run, trace, credit, now).id)
                state.runs[run.id] = replace(run, trace=tuple(trace.steps))
                continue
            after = _summary_with(state, lines=(rec.credit_line,))
        state.recommendations[rec.id] = replace(
            rec,
            based_on_financial_version=state.financial_version,
            details=rec.details
            | {"impact": {"before": impact(current), "after_if_executed": impact(after)}},
        )
    return follow_ups


def decide(
    state: DemoState,
    rec_id: UUID,
    approve: bool,
    note: str | None,
    now: datetime,
    credit_until: date,
    credit_rounding: Cents,
) -> tuple[Recommendation, Receipt, list[UUID]]:
    """Approve or reject a pending recommendation. Raises DecisionError for NOT_FOUND,
    NOT_PENDING and STALE_RECOMMENDATION; a stale recommendation is marked STALE first.

    Returns the recommendation, its DECISION receipt and the ids of follow-up
    recommendations made because an approval changed the data."""
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
        return _store(state, rec, receipt), receipt, []

    mandate = state.active_mandate
    if rec.kind is RecommendationKind.RESCHEDULE_PAYMENT:
        outflow = next(o for o in state.outflows if o.id == rec.outflow_id)
        decision = evaluate_move(
            outflow,
            rec.to_date,
            mandate,
            state.as_of,
            owner_approved=True,
            window_end=state.window_end,
        )
    else:
        decision = evaluate_credit(rec.credit_line, mandate, owner_approved=True)
    details = rec.details | {"checks": _checks(decision)}

    if decision.lane is Lane.NOT_ALLOWED:
        rec = replace(
            rec, status=RecommendationStatus.BLOCKED, decided_at=now, note=note, details=details
        )
        receipt = _receipt(state, rec, "DECISION", now, "Your decision", before, before, "APPROVED")
        return _store(state, rec, receipt), receipt, []

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
    rec = _store(state, rec, receipt)
    return rec, receipt, _refresh_pending(state, rec, now, credit_until, credit_rounding)


def request_reschedule(
    state: DemoState, run: Run, outflow: Outflow, to_date: date, now: datetime
) -> Run:
    """The owner asks to move a payment: same policy, with the owner's approval implied."""
    trace = Tracer(run)
    before_summary = state.forecast().summary
    decision = evaluate_move(
        outflow,
        to_date,
        state.active_mandate,
        state.as_of,
        owner_approved=True,
        window_end=state.window_end,
    )
    lane = decision.lane
    trace.add("POLICY", f"{outflow.counterparty}: {_lane_text(lane)}", now)
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
