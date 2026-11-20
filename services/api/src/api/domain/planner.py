"""Greedy planner: postpone movable payments until the balance stays above the floor.

The planner only proposes. Whether a move may happen (alone, with approval or not at all) is
decided later by the policy against the owner's mandate."""

from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import date, timedelta
from uuid import UUID

from api.domain.forecast import Breach, ForecastInput, ForecastSummary, compute_forecast
from api.domain.models import Category, Outflow
from api.money import Cents

PLANNER_VERSION = "greedy-1.0.0"
MIN_BENEFIT_DAYS = 3


@dataclass(frozen=True, slots=True)
class MoveBenefit:
    first_breach_delay_days: int
    days_below_floor_saved: int


@dataclass(frozen=True, slots=True)
class MoveCandidate:
    outflow_id: UUID
    ref: str
    counterparty: str
    category: Category
    amount: Cents
    from_date: date
    to_date: date
    benefit: MoveBenefit


@dataclass(frozen=True, slots=True)
class PlanResult:
    candidates: tuple[MoveCandidate, ...]
    resolved: bool
    residual_breach: Breach | None
    forecast_if_all_applied: ForecastSummary
    planner_version: str = PLANNER_VERSION


def _target(outflow: Outflow, max_delay_days: int) -> date | None:
    if outflow.latest_allowed_date is None:
        return None
    return min(outflow.latest_allowed_date, outflow.due_date + timedelta(days=max_delay_days))


def _breach_day(summary: ForecastSummary, window_end: date) -> date:
    """First breach date, or the day after the window when there is none."""
    return summary.first_breach.date if summary.first_breach else window_end + timedelta(days=1)


def _with_move(data: ForecastInput, outflow_id: UUID, to: date) -> ForecastInput:
    return replace(
        data,
        outflows=tuple(
            replace(o, scheduled_date=to) if o.id == outflow_id else o for o in data.outflows
        ),
    )


def plan_moves(
    data: ForecastInput,
    protected_categories: Collection[Category],
    max_delay_days: int,
) -> PlanResult:
    window_end = data.as_of + timedelta(days=data.horizon_days - 1)
    current = data
    summary = compute_forecast(current).summary
    chosen: list[MoveCandidate] = []
    moved: set[UUID] = set()

    while summary.first_breach is not None:
        breach = summary.first_breach.date
        eligible = []
        for outflow in current.outflows:
            target = _target(outflow, max_delay_days)
            if (
                target is None
                or outflow.id in moved
                or outflow.category in protected_categories
                or outflow.scheduled_date != outflow.due_date
                or not outflow.scheduled_date <= breach < target
            ):
                continue
            eligible.append((outflow, target))
        eligible.sort(key=lambda item: (-item[0].amount, -item[1].toordinal(), item[0].ref))

        accepted = None
        for outflow, target in eligible:
            candidate_input = _with_move(current, outflow.id, target)
            candidate_summary = compute_forecast(candidate_input).summary
            benefit = MoveBenefit(
                first_breach_delay_days=(
                    _breach_day(candidate_summary, window_end) - _breach_day(summary, window_end)
                ).days,
                days_below_floor_saved=summary.days_below_floor
                - candidate_summary.days_below_floor,
            )
            if max(benefit.first_breach_delay_days, benefit.days_below_floor_saved) >= (
                MIN_BENEFIT_DAYS
            ):
                accepted = (outflow, target, benefit, candidate_input, candidate_summary)
                break
        if accepted is None:
            break

        outflow, target, benefit, current, summary = accepted
        moved.add(outflow.id)
        chosen.append(
            MoveCandidate(
                outflow_id=outflow.id,
                ref=outflow.ref,
                counterparty=outflow.counterparty,
                category=outflow.category,
                amount=outflow.amount,
                from_date=outflow.scheduled_date,
                to_date=target,
                benefit=benefit,
            )
        )

    return PlanResult(
        candidates=tuple(chosen),
        resolved=summary.first_breach is None,
        residual_breach=summary.first_breach,
        forecast_if_all_applied=summary,
    )
