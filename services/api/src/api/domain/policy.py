"""Policy engine: decide the lane of each proposal against the owner's mandate.

Every rule is evaluated and recorded (passes included) so receipts show the full reasoning.
The strictest effect wins: NOT_ALLOWED > NEEDS_APPROVAL > DONE_WITHIN_MANDATE."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from api.domain.models import CreditLine, Mandate, Outflow
from api.money import Cents, format_eur

POLICY_VERSION = "policy-1.0.0"


class Lane(StrEnum):
    DONE_WITHIN_MANDATE = "DONE_WITHIN_MANDATE"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    NOT_ALLOWED = "NOT_ALLOWED"


class CheckResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INFO = "INFO"


_SEVERITY = {Lane.DONE_WITHIN_MANDATE: 0, Lane.NEEDS_APPROVAL: 1, Lane.NOT_ALLOWED: 2}


@dataclass(frozen=True, slots=True)
class Check:
    """`effect` is set only when `result` is FAIL."""

    rule: str
    result: CheckResult
    text: str
    effect: Lane | None = None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    lane: Lane
    checks: tuple[Check, ...]
    policy_version: str = POLICY_VERSION

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.result is CheckResult.FAIL)


@dataclass(frozen=True, slots=True)
class MoveProposal:
    outflow: Outflow
    to_date: date


def _decide(checks: list[Check], base: Lane = Lane.DONE_WITHIN_MANDATE) -> PolicyDecision:
    lane = max(
        [base, *(c.effect for c in checks if c.effect is not None)], key=_SEVERITY.__getitem__
    )
    return PolicyDecision(lane=lane, checks=tuple(checks))


def _passed(rule: str, text: str) -> Check:
    return Check(rule, CheckResult.PASS, text)


def _failed(rule: str, text: str, effect: Lane) -> Check:
    return Check(rule, CheckResult.FAIL, text, effect)


def _approved(rule: str, reason: str) -> Check:
    return _passed(rule, f"{reason}; approved by you")


def _pause_check(mandate: Mandate, owner_approved: bool) -> Check:
    if not mandate.paused:
        return _passed("PAUSE", "Vero is not paused")
    if owner_approved:
        return _approved("PAUSE", "Vero is paused")
    return _failed("PAUSE", "Vero is paused, so everything waits for you", Lane.NEEDS_APPROVAL)


def _floor_info(mandate: Mandate) -> Check:
    return Check("R1", CheckResult.INFO, f"Your floor is {format_eur(mandate.cash_floor)}")


def evaluate_move(
    outflow: Outflow,
    to_date: date,
    mandate: Mandate,
    as_of: date,
    already_done_today: Cents = 0,
    owner_approved: bool = False,
    window_end: date | None = None,
) -> PolicyDecision:
    """`window_end` is the last forecast day: a payment moved beyond it would drop out of the
    forecast, so such a date is refused."""
    checks: list[Check] = []

    if outflow.category in mandate.protected_categories:
        checks.append(_failed("R4", f"{outflow.category} payments are protected", Lane.NOT_ALLOWED))
    else:
        checks.append(_passed("R4", f"{outflow.category} payments are not protected"))

    max_by_mandate = outflow.due_date + timedelta(days=mandate.max_delay_days)
    if outflow.latest_allowed_date is None:
        validity = _failed("VALIDITY", "This payment cannot be moved", Lane.NOT_ALLOWED)
    elif to_date < as_of:
        validity = _failed("VALIDITY", f"{to_date} is before today ({as_of})", Lane.NOT_ALLOWED)
    elif to_date <= outflow.scheduled_date:
        validity = _failed(
            "VALIDITY", f"{to_date} is not later than {outflow.scheduled_date}", Lane.NOT_ALLOWED
        )
    elif to_date > outflow.latest_allowed_date:
        validity = _failed(
            "VALIDITY",
            f"{to_date} is after the latest allowed date {outflow.latest_allowed_date}",
            Lane.NOT_ALLOWED,
        )
    elif to_date > max_by_mandate:
        validity = _failed(
            "VALIDITY",
            f"{to_date} is more than {mandate.max_delay_days} days after the due date",
            Lane.NOT_ALLOWED,
        )
    elif window_end is not None and to_date > window_end:
        validity = _failed(
            "VALIDITY",
            f"{to_date} is after the forecast window, which ends on {window_end}",
            Lane.NOT_ALLOWED,
        )
    else:
        validity = _passed(
            "VALIDITY",
            f"{to_date} is within the allowed window (until {outflow.latest_allowed_date})",
        )
    checks.append(validity)

    checks.append(_pause_check(mandate, owner_approved))

    if mandate.act_alone_enabled:
        checks.append(_passed("R2", "You let Vero act alone"))
    elif owner_approved:
        checks.append(_approved("R2", "Acting alone is off"))
    else:
        checks.append(
            _failed("R2", "Acting alone is off, so this needs your approval", Lane.NEEDS_APPROVAL)
        )

    over_action = outflow.amount > mandate.max_amount_per_action
    over_day = already_done_today + outflow.amount > mandate.max_amount_per_day
    limits = (
        f"{format_eur(outflow.amount)} vs your limits of "
        f"{format_eur(mandate.max_amount_per_action)} per action and "
        f"{format_eur(mandate.max_amount_per_day)} per day"
    )
    if not over_action and not over_day:
        checks.append(_passed("R3", f"Within limits: {limits}"))
    elif owner_approved:
        checks.append(_approved("R3", f"Above limits: {limits}"))
    else:
        checks.append(_failed("R3", f"Above limits: {limits}", Lane.NEEDS_APPROVAL))

    checks.append(_floor_info(mandate))
    return _decide(checks)


def evaluate_credit(
    line: CreditLine, mandate: Mandate, owner_approved: bool = False
) -> PolicyDecision:
    checks: list[Check] = []
    if not mandate.credit_enabled:
        checks.append(_failed("R5", "Credit proposals are switched off", Lane.NOT_ALLOWED))
    elif line.amount > mandate.max_credit_amount:
        checks.append(
            _failed(
                "R5",
                f"{format_eur(line.amount)} is above your credit maximum of "
                f"{format_eur(mandate.max_credit_amount)}",
                Lane.NOT_ALLOWED,
            )
        )
    elif owner_approved:
        checks.append(_approved("R5", "Credit always needs your approval"))
    else:
        checks.append(
            _failed(
                "R5",
                f"Within your credit maximum of {format_eur(mandate.max_credit_amount)}, "
                "but credit always needs your approval",
                Lane.NEEDS_APPROVAL,
            )
        )
    checks.append(_pause_check(mandate, owner_approved))
    checks.append(_floor_info(mandate))
    return _decide(checks)


def evaluate_moves(
    proposals: Sequence[MoveProposal],
    mandate: Mandate,
    as_of: date,
    window_end: date | None = None,
) -> list[PolicyDecision]:
    """In order; moves Vero may make alone count towards the per-day limit of later ones."""
    done_today = 0
    decisions = []
    for proposal in proposals:
        decision = evaluate_move(
            proposal.outflow, proposal.to_date, mandate, as_of, done_today, window_end=window_end
        )
        if decision.lane is Lane.DONE_WITHIN_MANDATE:
            done_today += proposal.outflow.amount
        decisions.append(decision)
    return decisions
