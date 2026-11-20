"""State-changing operations. Callers hold the repository's write lock."""

from dataclasses import replace
from datetime import date, datetime
from uuid import uuid4

from fastapi import status

from api.domain.models import (
    Category,
    Event,
    EventType,
    Mandate,
    MandateStatus,
    PlannedEvent,
    PlannedEventKind,
    Run,
    RunMode,
    RunStatus,
)
from api.errors import ApiError, ErrorCode
from api.money import Cents
from api.state import DemoState

MAX_CLOSURE_DAYS = 366


def _invalid(detail: str) -> ApiError:
    return ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, detail)


def _day_month(day: date) -> str:
    return f"{day.day} {day:%b}"


def closure_label(date_from: date, date_to: date) -> str:
    if date_from == date_to:
        return f"You plan to close on {_day_month(date_from)}"
    return f"You plan to close {_day_month(date_from)} – {_day_month(date_to)}"


def plan_closure(
    state: DemoState,
    date_from: date,
    date_to: date,
    variable_costs_stop: bool,
    note: str | None,
    now: datetime,
) -> Run:
    """Record a planned closure, bump the financial version and complete a run.

    Until the agent and decision steps exist, the run finishes immediately in FALLBACK mode."""
    if date_to < date_from:
        raise _invalid("date_to must not be before date_from")
    if date_from < state.as_of:
        raise _invalid(f"a closure cannot start before today ({state.as_of})")
    if (date_to - date_from).days + 1 > MAX_CLOSURE_DAYS:
        raise _invalid(f"a closure can last at most {MAX_CLOSURE_DAYS} days")
    for existing in state.planned_events:
        if existing.date_from <= date_to and date_from <= existing.date_to:
            raise _invalid(
                f"overlaps the planned closure {existing.date_from} – {existing.date_to}"
            )

    state.planned_events.append(
        PlannedEvent(
            id=uuid4(),
            kind=PlannedEventKind.CLOSURE,
            date_from=date_from,
            date_to=date_to,
            variable_costs_stop=variable_costs_stop,
            note=note,
        )
    )
    state.financial_version += 1
    event = Event(
        id=uuid4(),
        type=EventType.CLOSURE_PLANNED,
        payload={
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "variable_costs_stop": variable_costs_stop,
            "note": note,
        },
        created_at=now,
        financial_version_after=state.financial_version,
    )
    state.events.append(event)
    run = Run(
        id=uuid4(),
        event_id=event.id,
        trigger_type=event.type,
        trigger_label=closure_label(date_from, date_to),
        status=RunStatus.SUCCEEDED,
        mode=RunMode.FALLBACK,
        created_at=now,
        finished_at=now,
        forecast_id=state.forecast_id,
    )
    state.runs[run.id] = run
    return run


REQUIRED_PROTECTED = frozenset({Category.PAYROLL, Category.SOCIAL_SECURITY, Category.TAX})


def _mandate_invalid(detail: str) -> ApiError:
    return ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.MANDATE_INVALID, detail)


def create_mandate_draft(
    state: DemoState,
    cash_floor: Cents,
    act_alone_enabled: bool,
    max_delay_days: int,
    max_amount_per_action: Cents,
    max_amount_per_day: Cents,
    protected_categories: frozenset[Category],
    credit_enabled: bool,
    max_credit_amount: Cents,
    note: str | None,
    now: datetime,
) -> Mandate:
    missing = REQUIRED_PROTECTED - protected_categories
    if missing:
        raise _mandate_invalid(f"{', '.join(sorted(missing))} must stay protected")
    if max_amount_per_action > max_amount_per_day:
        raise _mandate_invalid("max_amount_per_action cannot exceed max_amount_per_day")
    draft = Mandate(
        version=max(m.version for m in state.mandates) + 1,
        status=MandateStatus.DRAFT,
        cash_floor=cash_floor,
        act_alone_enabled=act_alone_enabled,
        max_delay_days=max_delay_days,
        max_amount_per_action=max_amount_per_action,
        max_amount_per_day=max_amount_per_day,
        protected_categories=protected_categories,
        credit_enabled=credit_enabled,
        max_credit_amount=max_credit_amount,
        paused=False,
        created_at=now,
        note=note,
    )
    state.mandates.append(draft)
    return draft


def activate_mandate(state: DemoState, version: int, now: datetime) -> Mandate:
    """Make a draft the active mandate. The pause switch carries over from the previous one."""
    target = next((m for m in state.mandates if m.version == version), None)
    if target is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, f"no mandate version {version}"
        )
    if target.status is not MandateStatus.DRAFT:
        raise _mandate_invalid(f"mandate version {version} is {target.status}, not a draft")
    previous = state.active_mandate
    activated = replace(
        target, status=MandateStatus.ACTIVE, activated_at=now, paused=previous.paused
    )
    state.mandates = [
        activated
        if m.version == version
        else replace(m, status=MandateStatus.SUPERSEDED)
        if m.version == previous.version
        else m
        for m in state.mandates
    ]
    return activated


def set_paused(state: DemoState, paused: bool) -> Mandate:
    active = state.active_mandate
    updated = replace(active, paused=paused)
    state.mandates = [updated if m.version == active.version else m for m in state.mandates]
    return updated
