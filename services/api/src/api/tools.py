"""Read-only tools the AI agent calls through /internal/v1 (DATA_CONTRACTS §3.2).

Every call is bound to a RUNNING run and the financial version it started on, is idempotent per
`call_id`, counts towards the run's limit and adds a plain-language step to the run's trace.
Tools never change financial data."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.config import Settings
from api.decisions import Tracer, moved_alone_today
from api.domain.credit import size_credit_line
from api.domain.forecast import ForecastInput, compute_forecast
from api.domain.models import Category, CreditLine, Run, RunStatus
from api.domain.policy import evaluate_credit, evaluate_move
from api.money import Money, format_eur
from api.schemas import Driver, ForecastSummary, Mandate, Outflow, PlannedEvent, RevenueModel
from api.state import DemoState


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class ExplainArgs(_Args):
    day: date = Field(
        alias="date", description="Business date (YYYY-MM-DD) to look around, ±7 days"
    )


class ListOutflowsArgs(_Args):
    movable_only: bool = Field(default=False, description="Only payments that may be postponed")


class MoveArg(_Args):
    outflow_id: UUID
    to_date: date


class SimulateArgs(_Args):
    moves: list[MoveArg] = Field(min_length=1, description="Moves from find_reschedule_options")


class SizeCreditArgs(_Args):
    after_moves: list[MoveArg] = Field(
        default_factory=list, description="Moves to assume first (from find_reschedule_options)"
    )


class CreditArg(_Args):
    amount: Money
    available_from: date


class ProposalArg(_Args):
    kind: Literal["RESCHEDULE_PAYMENT", "CREDIT_LINE"]
    move: MoveArg | None = None
    credit_line: CreditArg | None = None


class CheckPolicyArgs(_Args):
    proposal: ProposalArg


@dataclass(frozen=True)
class ToolOutput:
    result: Any
    label: str
    result_label: str | None = None
    log: dict | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[_Args]
    handler: Callable[[DemoState, Run, Any, Settings], ToolOutput]


def _day(d: date) -> str:
    return f"{d.day} {d:%b}"


def _summary(summary) -> dict:
    return ForecastSummary.from_domain(summary).model_dump(mode="json")


def _outflows(state: DemoState, items) -> list[dict]:
    mandate = state.active_mandate
    return [Outflow.from_domain(o, mandate).model_dump(mode="json") for o in items]


def _check_moves(run: Run, moves: list[MoveArg]) -> None:
    allowed = {tuple(c) for c in (run.tool_log or {}).get("candidates", [])}
    for move in moves:
        if (str(move.outflow_id), move.to_date.isoformat()) not in allowed:
            raise ToolError(
                "INVALID_ARGUMENT",
                "each move must be copied from a find_reschedule_options candidate in this run",
            )


def _with_moves(data: ForecastInput, moves: list[MoveArg]) -> ForecastInput:
    targets = {m.outflow_id: m.to_date for m in moves}
    return replace(
        data,
        outflows=tuple(
            replace(o, scheduled_date=targets[o.id]) if o.id in targets else o
            for o in data.outflows
        ),
    )


def _get_mandate(state, run, args, settings) -> ToolOutput:
    mandate = state.active_mandate
    return ToolOutput(
        Mandate.from_domain(mandate).model_dump(mode="json"),
        f"Reading your rules (v{mandate.version})",
    )


def _get_forecast(state, run, args, settings) -> ToolOutput:
    forecast = state.forecast()
    summary = forecast.summary
    if summary.first_breach:
        outcome = (
            f"Below your {format_eur(forecast.cash_floor)} floor from "
            f"{_day(summary.first_breach.date)}, lowest {format_eur(summary.lowest.closing)} "
            f"on {_day(summary.lowest.date)}"
        )
    else:
        outcome = "Your cash stays above your floor for the next 13 weeks"
    return ToolOutput(
        {
            "cash_floor": str(forecast.cash_floor),
            "summary": _summary(summary),
            "drivers": [
                Driver(
                    kind=d.kind,
                    ref=d.ref,
                    label=d.label,
                    amount=d.amount,
                    date_from=d.date_from,
                    date_to=d.date_to,
                ).model_dump(mode="json")
                for d in forecast.drivers
            ],
            "planned_events": [
                PlannedEvent.from_domain(e).model_dump(mode="json") for e in state.planned_events
            ],
        },
        "Recomputing your cash for the next 91 days",
        outcome,
    )


def _explain_drivers(state, run, args: ExplainArgs, settings) -> ToolOutput:
    if not state.as_of <= args.day <= state.window_end:
        raise ToolError(
            "INVALID_ARGUMENT", f"date must be between {state.as_of} and {state.window_end}"
        )
    start, end = args.day - timedelta(days=7), args.day + timedelta(days=7)
    days = [d for d in state.forecast().days if start <= d.date <= end]
    return ToolOutput(
        {
            "date": args.day.isoformat(),
            "closed": any(d.closed for d in days if d.date == args.day),
            "revenue_estimate": str(sum(d.revenue for d in days)),
            "outflows": _outflows(
                state, [o for o in state.outflows if start <= o.scheduled_date <= end]
            ),
            "days": [{"date": d.date.isoformat(), "closing": str(d.closing)} for d in days],
        },
        f"Looking at what happens around {_day(args.day)}",
    )


def _get_revenue_model(state, run, args, settings) -> ToolOutput:
    return ToolOutput(
        RevenueModel.from_domain(state.revenue_model).model_dump(mode="json"),
        "Checking your usual weekly takings",
    )


def _list_outflows(state, run, args: ListOutflowsArgs, settings) -> ToolOutput:
    mandate = state.active_mandate
    items = [
        o
        for o in state.outflows
        if not args.movable_only
        or (o.latest_allowed_date is not None and o.category not in mandate.protected_categories)
    ]
    label = "Listing payments that could wait" if args.movable_only else "Listing your payments"
    return ToolOutput(_outflows(state, items), label)


def _find_reschedule_options(state, run, args, settings) -> ToolOutput:
    plan = state.plan()
    candidates = [
        {
            "outflow_id": str(c.outflow_id),
            "ref": c.ref,
            "counterparty": c.counterparty,
            "category": c.category.value,
            "amount": str(c.amount),
            "from_date": c.from_date.isoformat(),
            "to_date": c.to_date.isoformat(),
            "benefit": {
                "first_breach_delay_days": c.benefit.first_breach_delay_days,
                "days_below_floor_saved": c.benefit.days_below_floor_saved,
            },
        }
        for c in plan.candidates
    ]
    count = len(candidates)
    return ToolOutput(
        {
            "candidates": candidates,
            "resolved": plan.resolved,
            "residual_breach": _summary(plan.forecast_if_all_applied)["first_breach"],
            "forecast_if_all_applied": _summary(plan.forecast_if_all_applied),
            "planner_version": plan.planner_version,
        },
        "Looking for payments that can wait",
        f"Found {count} payment{'s' if count != 1 else ''} worth postponing"
        if count
        else "No payment can usefully wait",
        {"candidates": [[c["outflow_id"], c["to_date"]] for c in candidates]},
    )


def _simulate_moves(state, run, args: SimulateArgs, settings) -> ToolOutput:
    _check_moves(run, args.moves)
    summary = compute_forecast(_with_moves(state.forecast_input(), args.moves)).summary
    count = len(args.moves)
    return ToolOutput(
        {"summary": _summary(summary)}, f"Testing {count} move{'s' if count != 1 else ''}"
    )


def _size_credit_line(state, run, args: SizeCreditArgs, settings) -> ToolOutput:
    _check_moves(run, args.after_moves)
    sizing = size_credit_line(
        _with_moves(state.forecast_input(), args.after_moves),
        settings.credit_line_available_until,
        settings.credit_line_rounding,
    )
    line = sizing.credit_line
    result = {
        "needed": sizing.needed,
        "credit_line": {
            "amount": str(line.amount),
            "available_from": line.available_from.isoformat(),
            "available_until": line.available_until.isoformat(),
            "shortfall": str(sizing.shortfall),
            "rounded_to": str(sizing.rounded_to),
            "headroom_at_lowest": str(sizing.headroom_at_lowest),
        }
        if line
        else None,
    }
    return ToolOutput(
        result,
        "Sizing a credit line for what's left",
        f"{format_eur(line.amount)} from {_day(line.available_from)} would cover the gap"
        if line
        else "No credit needed",
        {"credit": [str(line.amount), line.available_from.isoformat()] if line else None},
    )


def _get_fx_exposure(state, run, args, settings) -> ToolOutput:
    paid = [
        t for t in state.history if t.category is Category.PARTNER_TRANSFER and t.original_currency
    ]
    upcoming = [o for o in state.outflows if o.original_currency]
    currency = (paid or upcoming)[0].original_currency if (paid or upcoming) else None
    amounts = [-t.amount for t in paid]
    return ToolOutput(
        {
            "currency": currency,
            "tranches_paid": len(paid),
            "eur_min": str(min(amounts)) if amounts else None,
            "eur_max": str(max(amounts)) if amounts else None,
            "last_rate": paid[-1].fx_rate if paid else None,
            "upcoming": _outflows(state, upcoming),
        },
        f"Checking your {currency} transfers" if currency else "Checking foreign-currency payments",
    )


def _check_policy(state, run, args: CheckPolicyArgs, settings) -> ToolOutput:
    proposal, mandate = args.proposal, state.active_mandate
    if proposal.kind == "RESCHEDULE_PAYMENT":
        if proposal.move is None:
            raise ToolError("INVALID_ARGUMENT", "a RESCHEDULE_PAYMENT proposal needs `move`")
        outflow = next((o for o in state.outflows if o.id == proposal.move.outflow_id), None)
        if outflow is None:
            raise ToolError("INVALID_ARGUMENT", f"no outflow {proposal.move.outflow_id}")
        decision = evaluate_move(
            outflow, proposal.move.to_date, mandate, state.as_of, moved_alone_today(state)
        )
        what = outflow.counterparty
    else:
        if proposal.credit_line is None:
            raise ToolError("INVALID_ARGUMENT", "a CREDIT_LINE proposal needs `credit_line`")
        line = CreditLine(
            proposal.credit_line.amount,
            proposal.credit_line.available_from,
            settings.credit_line_available_until,
        )
        decision = evaluate_credit(line, mandate)
        what = f"Credit line of {format_eur(line.amount)}"
    return ToolOutput(
        {
            "lane_preview": decision.lane.value,
            "checks": [
                {"rule": c.rule, "result": c.result.value, "effect": c.effect, "text": c.text}
                for c in decision.checks
            ],
        },
        "Checking this against your rules",
        f"{what}: {decision.lane.value.replace('_', ' ').lower()} (preview)",
    )


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "get_mandate",
            "The owner's active rules: cash floor, whether Vero may act alone, limits per action "
            "and per day, protected categories, credit settings, pause switch.",
            NoArgs,
            _get_mandate,
        ),
        Tool(
            "get_forecast",
            "The 91-day cash forecast summary: first day below the floor, lowest point, days "
            "below the floor, and drivers (why). Call this first.",
            NoArgs,
            _get_forecast,
        ),
        Tool(
            "explain_drivers",
            "Daily balances, payments and expected revenue within 7 days of a date, to explain "
            "what happens around a breach.",
            ExplainArgs,
            _explain_drivers,
        ),
        Tool(
            "get_revenue_model",
            "The usual card revenue and variable costs per weekday, learned from recent weeks.",
            NoArgs,
            _get_revenue_model,
        ),
        Tool(
            "list_outflows",
            "Known upcoming payments with dates, categories and whether they may be postponed.",
            ListOutflowsArgs,
            _list_outflows,
        ),
        Tool(
            "find_reschedule_options",
            "The planner's payment postponements that help the cash gap, in order, with their "
            "benefit. Proposals must copy outflow_id and to_date from these candidates.",
            NoArgs,
            _find_reschedule_options,
        ),
        Tool(
            "simulate_moves",
            "The forecast summary if some candidate moves were applied. Read-only.",
            SimulateArgs,
            _simulate_moves,
        ),
        Tool(
            "size_credit_line",
            "Size a short-term credit line for the gap left after optional candidate moves. "
            "A credit line proposal must copy amount and available_from from this result.",
            SizeCreditArgs,
            _size_credit_line,
        ),
        Tool(
            "get_fx_exposure",
            "Foreign-currency transfers (e.g. CHF): what they cost so far and upcoming ones.",
            NoArgs,
            _get_fx_exposure,
        ),
        Tool(
            "check_policy",
            "Preview the lane of a proposal against the owner's rules (done alone, needs "
            "approval, not allowed) with every check. Not binding.",
            CheckPolicyArgs,
            _check_policy,
        ),
    )
}


def _inline(schema: dict) -> dict:
    """JSON Schema with $defs references inlined, which LLM tool APIs handle best."""
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def tool_specs() -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": _inline(t.args.model_json_schema()),
        }
        for t in TOOLS.values()
    ]


def _error(code: str, message: str, state: DemoState, used: int) -> dict:
    return {
        "ok": False,
        "result": None,
        "error": {"code": code, "message": message},
        "financial_version": state.financial_version,
        "tool_calls_used": used,
    }


def call_tool(
    state: DemoState,
    settings: Settings,
    name: str,
    run_id: UUID,
    call_id: str,
    arguments: dict,
    now,
) -> dict:
    run = state.runs.get(run_id)
    if run is None or run.status is not RunStatus.RUNNING:
        return _error("RUN_CLOSED", f"run {run_id} is not running", state, 0)
    log = dict(run.tool_log or {})
    calls = dict(log.get("calls", {}))
    used = log.get("used", 0)
    if call_id in calls:
        return calls[call_id]

    tool = TOOLS.get(name)
    if tool is None:
        response = _error("UNKNOWN_TOOL", f"no tool {name!r}", state, used)
    elif used >= settings.agent_max_tool_calls:
        response = _error(
            "TOOL_LIMIT_REACHED",
            f"limit of {settings.agent_max_tool_calls} calls reached",
            state,
            used,
        )
    elif state.financial_version != run.started_financial_version:
        response = _error("STALE_RUN", "the data changed since this run started; stop", state, used)
    else:
        used += 1
        tracer = Tracer(run)
        try:
            output = tool.handler(state, run, tool.args.model_validate(arguments), settings)
        except ValidationError as exc:
            message = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            response = _error("INVALID_ARGUMENT", message, state, used)
            tracer.add("TOOL_CALL", f"Tool {name}: invalid arguments", now, name)
        except ToolError as exc:
            response = _error(exc.code, exc.message, state, used)
            tracer.add("TOOL_CALL", f"Tool {name}: {exc.message}", now, name)
        else:
            response = {
                "ok": True,
                "result": output.result,
                "error": None,
                "financial_version": state.financial_version,
                "tool_calls_used": used,
            }
            tracer.add("TOOL_CALL", output.label, now, name, {"arguments": arguments})
            if output.result_label:
                tracer.add("TOOL_RESULT", output.result_label, now, name)
            log |= output.log or {}
        run = replace(run, trace=tuple(tracer.steps))

    calls[call_id] = response
    state.runs[run_id] = replace(run, tool_log=log | {"calls": calls, "used": used})
    return response
