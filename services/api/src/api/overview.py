"""The dashboard: what Vero found, did, proposes and refused."""

from api.domain.forecast import DriverKind
from api.domain.models import Category, RecommendationStatus
from api.money import format_eur
from api.schemas import (
    Company,
    Explanation,
    Finding,
    ForecastSummary,
    Lanes,
    Overview,
    Recommendation,
    RunRef,
)
from api.state import DemoState

HIDDEN = {RecommendationStatus.SUPERSEDED, RecommendationStatus.STALE}


def findings(state: DemoState) -> list[Finding]:
    forecast = state.forecast()
    result = [
        Finding(kind="DRIVER", text=d.label)
        for d in forecast.drivers
        if d.kind is DriverKind.NO_REVENUE
    ]
    if state.recurring:
        names = ", ".join(r.counterparty for r in state.recurring)
        result.append(
            Finding(kind="RECURRING", text=f"{len(state.recurring)} recurring payments: {names}")
        )
    tranches = [
        t for t in state.history if t.category is Category.PARTNER_TRANSFER and t.original_currency
    ]
    if tranches:
        amounts = [-t.amount for t in tranches]
        rates = sorted(t.fx_rate for t in tranches)
        result.append(
            Finding(
                kind="FX_EXPOSURE",
                text=f"{len(tranches)} {tranches[0].original_currency} transfers so far cost "
                f"{format_eur(min(amounts))} – {format_eur(max(amounts))} "
                f"(rate {rates[0]} – {rates[-1]})",
            )
        )
    if state.planned_events:
        backtest = state.backtest()
        result.append(
            Finding(
                kind="BACKTEST",
                text=f"On the sample year the forecast low was "
                f"{format_eur(backtest.forecast.lowest.closing)} on "
                f"{backtest.forecast.lowest.date:%d %b}; the real low was "
                f"{format_eur(backtest.actual.lowest.closing)} on "
                f"{backtest.actual.lowest.date:%d %b}",
            )
        )
    return result


def build_overview(state: DemoState) -> Overview:
    forecast = state.forecast()
    recs = sorted(
        (r for r in state.recommendations.values() if r.status not in HIDDEN),
        key=lambda r: r.created_at,
    )
    latest = max(state.runs.values(), key=lambda r: r.created_at, default=None)
    source = "TEMPLATE"
    if latest is not None and latest.headline:
        headline, source = latest.headline, latest.headline_source
    elif forecast.summary.first_breach is None:
        headline = (
            f"Your cash stays above your {format_eur(state.active_mandate.cash_floor)} floor "
            "for the next 13 weeks."
        )
    else:
        headline = (
            f"Your cash falls below your floor on {forecast.summary.first_breach.date:%d %b}."
        )
    return Overview(
        company=Company.from_state(state),
        cash_floor=state.active_mandate.cash_floor,
        forecast_summary=ForecastSummary.from_domain(forecast.summary),
        headline=Explanation(text=headline, source=source),
        lanes=Lanes(
            ANALYSED=findings(state),
            DONE_WITHIN_MANDATE=[
                Recommendation.from_domain(r) for r in recs if r.lane == "DONE_WITHIN_MANDATE"
            ],
            NEEDS_APPROVAL=[
                Recommendation.from_domain(r) for r in recs if r.lane == "NEEDS_APPROVAL"
            ],
            NOT_ALLOWED=[Recommendation.from_domain(r) for r in recs if r.lane == "NOT_ALLOWED"],
        ),
        pending_count=sum(
            r.status is RecommendationStatus.PENDING for r in state.recommendations.values()
        ),
        latest_run=RunRef(run_id=latest.id, status=latest.status) if latest else None,
    )
