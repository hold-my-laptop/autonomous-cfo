"""Propagation (docs/PROOF.md §5.8): what a classified finding means for the payments and the
Vero Cash forecast. Every number comes from the engine; nothing here changes the schedule (an
amount change is only proposed, and executed after approval, P14)."""

from datetime import date

from api.domain.forecast import ForecastInput
from api.domain.proof.cost_memory import CostItem
from api.domain.proof.engine import ENGINE_VERSION, FormulaError, compute
from api.domain.proof.findings import (
    Classification,
    FindingKind,
    FindingPropagation,
    FindingStatus,
    ForecastEffect,
    PaymentDecision,
    ProofFinding,
)
from api.money import Cents, format_eur


def _window(
    prefix: str, effective_from: date, billed: Cents, justified: Cents, data: ForecastInput
) -> dict | None:
    try:
        result = compute(
            "window_impact",
            {
                "outflow_ref_prefix": prefix,
                "effective_from": effective_from.isoformat(),
                "billed": str(billed),
                "justified": str(justified),
            },
            data,
        )
    except FormulaError:
        return None
    return result.outputs.model_dump(mode="json")


def _annual(monthly: Cents) -> Cents:
    return int(compute("annualize", {"monthly": str(monthly)}).outputs.yearly)


def propagate(
    finding: ProofFinding, cost_item: CostItem | None, forecast: ForecastInput
) -> FindingPropagation | None:
    """The payment decision, the disputed amounts and the forecast effect of a classified
    finding; None while it is not classified. Only a CONFLICT has a disputed amount: an
    unexplained change keeps the current payment while the owner reviews it."""
    if finding.status not in (
        FindingStatus.CLASSIFIED,
        FindingStatus.PENDING_APPROVAL,
        FindingStatus.RESOLVED,
    ):
        return None
    investigation = finding.investigation
    if finding.kind is FindingKind.INVOICE_PRICES:
        return FindingPropagation(
            decision=PaymentDecision.PAID,
            cost_ref=finding.cost_ref,
            effective_from=finding.period_from,
            billed_amount=finding.impact.billed,
            disputed=investigation.disputed if investigation else None,
            engine_version=ENGINE_VERSION,
        )

    current = finding.impact.expected
    billed = finding.impact.billed
    justified = investigation.justified if investigation else None
    if finding.classification is Classification.VERIFIED:
        proposed = billed
    elif finding.classification is Classification.CONFLICT and justified is not None:
        proposed = justified
    else:
        proposed = current
    decision = PaymentDecision.KEEP if proposed == current else PaymentDecision.UPDATE
    disputed = None
    if finding.classification is Classification.CONFLICT and billed is not None:
        disputed = billed - proposed if proposed is not None and billed > proposed else None

    prefix = cost_item.outflow_ref_prefix if cost_item else None
    start = finding.period_from
    effect = in_window = None
    if prefix and start and current is not None and proposed is not None:
        change = _window(prefix, start, proposed, current, forecast)
        if change is not None:
            effect = ForecastEffect(
                payments=tuple(change["payments"]),
                outflow_difference=int(change["outflow_difference"]),
                lowest_before=int(change["lowest_justified"]),
                lowest_after=int(change["lowest_billed"]),
            )
        if disputed and billed is not None:
            window = _window(prefix, start, billed, proposed, forecast)
            in_window = int(window["outflow_difference"]) if window else None
    monthly = cost_item is not None and cost_item.frequency == "MONTHLY"
    return FindingPropagation(
        decision=decision,
        cost_ref=finding.cost_ref,
        effective_from=start,
        current_amount=current,
        billed_amount=billed,
        proposed_amount=proposed,
        disputed=disputed,
        disputed_annual=_annual(disputed) if disputed and monthly else None,
        disputed_in_window=in_window,
        forecast=effect,
        engine_version=ENGINE_VERSION,
    )


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def describe(propagation: FindingPropagation) -> str:
    """One plain line for the run's trace."""
    p = propagation
    if p.decision is PaymentDecision.PAID:
        if p.disputed:
            return f"Already paid; {format_eur(p.disputed)} to ask back"
        return "Already paid: nothing to change"
    if p.decision is PaymentDecision.KEEP:
        line = f"Keep paying {format_eur(p.current_amount)} for {p.cost_ref}"
        if p.disputed_in_window:
            line += f"; {format_eur(p.disputed_in_window)} disputed in the next 13 weeks"
        return line
    line = f"Proposed: pay {format_eur(p.proposed_amount)} for {p.cost_ref}"
    if p.effective_from:
        line += f" from {_day(p.effective_from)}"
    if p.forecast and p.forecast.payments:
        count = len(p.forecast.payments)
        line += (
            f" ({count} payment{'s' if count != 1 else ''} in the next 13 weeks, "
            f"{format_eur(p.forecast.outflow_difference)} more)"
        )
    return line
