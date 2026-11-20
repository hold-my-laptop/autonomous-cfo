from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from api.config import get_settings
from api.domain.models import (
    Category,
    CreditLine,
    Mandate,
    MandateStatus,
    Outflow,
    OutflowSource,
    PlannedEvent,
    PlannedEventKind,
)
from api.domain.policy import (
    POLICY_VERSION,
    CheckResult,
    Lane,
    MoveProposal,
    evaluate_credit,
    evaluate_move,
    evaluate_moves,
)
from api.state import build_demo_state

AS_OF = date(2025, 5, 5)
DUE = date(2025, 6, 15)


def mandate(**changes) -> Mandate:
    values = dict(
        version=2,
        status=MandateStatus.ACTIVE,
        cash_floor=-500_000,
        act_alone_enabled=True,
        max_delay_days=45,
        max_amount_per_action=500_000,
        max_amount_per_day=500_000,
        protected_categories=frozenset({Category.PAYROLL, Category.SOCIAL_SECURITY, Category.TAX}),
        credit_enabled=True,
        max_credit_amount=3_000_000,
        paused=False,
        created_at=datetime(2025, 5, 5, tzinfo=UTC),
    )
    return Mandate(**(values | changes))


def outflow(category=Category.PARTNER_TRANSFER, amount=433_334, latest=45, ref="CHF-06"):
    return Outflow(
        id=uuid4(),
        ref=ref,
        counterparty="Luigi",
        category=category,
        amount=amount,
        due_date=DUE,
        scheduled_date=DUE,
        latest_allowed_date=DUE + timedelta(days=latest) if latest else None,
        source=OutflowSource.DETECTED,
    )


TO = date(2025, 7, 30)
LINE = CreditLine(2_000_000, date(2025, 6, 27), date(2025, 9, 30))


def rules(decision) -> list[str]:
    return [c.rule for c in decision.checks]


def failed(decision) -> dict[str, Lane]:
    return {c.rule: c.effect for c in decision.failed}


def test_move_within_mandate_is_done_alone() -> None:
    decision = evaluate_move(outflow(), TO, mandate(), AS_OF)

    assert decision.lane is Lane.DONE_WITHIN_MANDATE
    assert rules(decision) == ["R4", "VALIDITY", "PAUSE", "R2", "R3", "R1"]
    assert decision.failed == ()
    assert decision.checks[-1].result is CheckResult.INFO
    assert decision.policy_version == POLICY_VERSION


def test_act_alone_off_needs_approval() -> None:
    decision = evaluate_move(outflow(), TO, mandate(act_alone_enabled=False), AS_OF)
    assert decision.lane is Lane.NEEDS_APPROVAL
    assert failed(decision) == {"R2": Lane.NEEDS_APPROVAL}


@pytest.mark.parametrize(
    ("changes", "done_today"),
    [({"max_amount_per_action": 400_000, "max_amount_per_day": 500_000}, 0), ({}, 100_000)],
)
def test_limits_need_approval(changes: dict, done_today: int) -> None:
    decision = evaluate_move(outflow(), TO, mandate(**changes), AS_OF, done_today)
    assert decision.lane is Lane.NEEDS_APPROVAL
    assert failed(decision) == {"R3": Lane.NEEDS_APPROVAL}


def test_paused_needs_approval_for_everything() -> None:
    paused = mandate(paused=True)
    assert evaluate_move(outflow(), TO, paused, AS_OF).lane is Lane.NEEDS_APPROVAL
    assert failed(evaluate_move(outflow(), TO, paused, AS_OF)) == {"PAUSE": Lane.NEEDS_APPROVAL}
    assert evaluate_credit(LINE, paused).lane is Lane.NEEDS_APPROVAL


def test_protected_category_is_not_allowed() -> None:
    decision = evaluate_move(outflow(category=Category.PAYROLL), TO, mandate(), AS_OF)
    assert decision.lane is Lane.NOT_ALLOWED
    assert failed(decision)["R4"] is Lane.NOT_ALLOWED


@pytest.mark.parametrize(
    ("item", "to_date", "fragment"),
    [
        (outflow(latest=0), TO, "cannot be moved"),
        (outflow(), date(2025, 5, 1), "before today"),
        (outflow(), DUE, "not later than"),
        (outflow(latest=10), TO, "after the latest allowed date"),
        (outflow(latest=90), date(2025, 8, 20), "more than 45 days"),
    ],
)
def test_invalid_dates_are_not_allowed(item: Outflow, to_date: date, fragment: str) -> None:
    decision = evaluate_move(item, to_date, mandate(), AS_OF)
    assert decision.lane is Lane.NOT_ALLOWED
    validity = next(c for c in decision.checks if c.rule == "VALIDITY")
    assert validity.result is CheckResult.FAIL
    assert fragment in validity.text


def test_strictest_effect_wins() -> None:
    decision = evaluate_move(
        outflow(category=Category.TAX, latest=0), TO, mandate(paused=True), AS_OF
    )
    assert decision.lane is Lane.NOT_ALLOWED
    assert failed(decision) == {
        "R4": Lane.NOT_ALLOWED,
        "VALIDITY": Lane.NOT_ALLOWED,
        "PAUSE": Lane.NEEDS_APPROVAL,
    }


def test_owner_approval_lifts_pause_r2_r3_but_not_r4_or_validity() -> None:
    strict = mandate(paused=True, act_alone_enabled=False, max_amount_per_action=100_000)
    approved = evaluate_move(outflow(), TO, strict, AS_OF, owner_approved=True)
    assert approved.lane is Lane.DONE_WITHIN_MANDATE
    assert all(
        "approved by you" in c.text for c in approved.checks if c.rule in {"PAUSE", "R2", "R3"}
    )

    payroll = evaluate_move(
        outflow(category=Category.PAYROLL), TO, strict, AS_OF, owner_approved=True
    )
    assert payroll.lane is Lane.NOT_ALLOWED
    late = evaluate_move(outflow(latest=10), TO, strict, AS_OF, owner_approved=True)
    assert late.lane is Lane.NOT_ALLOWED


def test_credit_always_needs_approval() -> None:
    decision = evaluate_credit(LINE, mandate())
    assert decision.lane is Lane.NEEDS_APPROVAL
    assert failed(decision) == {"R5": Lane.NEEDS_APPROVAL}
    assert rules(decision) == ["R5", "PAUSE", "R1"]
    assert evaluate_credit(LINE, mandate(), owner_approved=True).lane is Lane.DONE_WITHIN_MANDATE


@pytest.mark.parametrize("changes", [{"credit_enabled": False}, {"max_credit_amount": 1_500_000}])
def test_credit_off_or_too_large_is_not_allowed(changes: dict) -> None:
    decision = evaluate_credit(LINE, mandate(**changes))
    assert decision.lane is Lane.NOT_ALLOWED
    assert evaluate_credit(LINE, mandate(**changes), owner_approved=True).lane is Lane.NOT_ALLOWED


def test_moves_done_alone_count_towards_the_daily_limit() -> None:
    first, second = outflow(ref="A"), outflow(ref="B")
    decisions = evaluate_moves(
        [MoveProposal(first, TO), MoveProposal(second, TO)], mandate(), AS_OF
    )
    assert [d.lane for d in decisions] == [Lane.DONE_WITHIN_MANDATE, Lane.NEEDS_APPROVAL]

    approval_needed = evaluate_moves(
        [MoveProposal(first, TO), MoveProposal(second, TO)], mandate(act_alone_enabled=False), AS_OF
    )
    assert [d.lane for d in approval_needed] == [Lane.NEEDS_APPROVAL, Lane.NEEDS_APPROVAL]


SETTINGS = get_settings()
needs_sample = pytest.mark.skipif(not SETTINGS.statement_path.exists(), reason="no sample CSV")


def demo_decisions(active: Mandate):
    state = build_demo_state(SETTINGS)
    state.planned_events.append(
        PlannedEvent(uuid4(), PlannedEventKind.CLOSURE, date(2025, 6, 1), date(2025, 6, 30))
    )
    state.mandates = [active]
    plan = state.plan()
    by_id = {o.id: o for o in state.outflows}
    moves = evaluate_moves(
        [MoveProposal(by_id[c.outflow_id], c.to_date) for c in plan.candidates], active, state.as_of
    )
    sizing = state.credit_sizing(
        SETTINGS.credit_line_available_until, SETTINGS.credit_line_rounding
    )
    return state, moves, evaluate_credit(sizing.credit_line, active)


@needs_sample
def test_demo_with_mandate_v2() -> None:
    state, moves, credit = demo_decisions(mandate())

    assert [m.lane for m in moves] == [Lane.DONE_WITHIN_MANDATE]
    assert credit.lane is Lane.NEEDS_APPROVAL


@needs_sample
def test_demo_with_mandate_v1() -> None:
    v1 = replace(build_demo_state(SETTINGS).active_mandate)
    _, moves, credit = demo_decisions(v1)

    assert [m.lane for m in moves] == [Lane.NEEDS_APPROVAL]
    assert credit.lane is Lane.NEEDS_APPROVAL


@needs_sample
def test_demo_payroll_move_is_not_allowed() -> None:
    state = build_demo_state(SETTINGS)
    payroll = next(o for o in state.outflows if o.ref == "PAY-06")
    decision = evaluate_move(payroll, date(2025, 7, 15), mandate(), state.as_of)

    assert decision.lane is Lane.NOT_ALLOWED
    assert failed(decision) == {
        "R4": Lane.NOT_ALLOWED,
        "VALIDITY": Lane.NOT_ALLOWED,
        "R3": Lane.NEEDS_APPROVAL,
    }


@needs_sample
def test_demo_paused_needs_approval_for_everything() -> None:
    _, moves, credit = demo_decisions(mandate(paused=True))
    assert [m.lane for m in moves] == [Lane.NEEDS_APPROVAL]
    assert credit.lane is Lane.NEEDS_APPROVAL


def test_moving_past_the_forecast_window_is_not_allowed() -> None:
    window_end = TO - timedelta(days=1)

    decision = evaluate_move(outflow(), TO, mandate(), AS_OF, window_end=window_end)

    assert decision.lane is Lane.NOT_ALLOWED
    assert failed(decision) == {"VALIDITY": Lane.NOT_ALLOWED}
    assert evaluate_move(outflow(), TO, mandate(), AS_OF, window_end=TO).lane is (
        Lane.DONE_WITHIN_MANDATE
    )
