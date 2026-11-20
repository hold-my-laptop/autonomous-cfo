"""Plain-language texts for a finished run. The AI service words them from the facts sent;
when it cannot answer, the backend templates written during the run stay."""

from dataclasses import replace
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from api.domain.forecast import ForecastSummary as DomainSummary
from api.domain.models import Recommendation as DomainRecommendation
from api.domain.models import Run, RunStatus
from api.overview import findings
from api.schemas import ForecastSummary, Mandate, Recommendation
from api.state import DemoState


class ExplainText(BaseModel):
    recommendation_id: UUID
    text: str = Field(min_length=1, max_length=300)


class ExplainResult(BaseModel):
    headline: str = Field(min_length=1, max_length=200)
    items: list[ExplainText]
    source: Literal["MODEL", "REPLAY", "TEMPLATE"]
    fallback_reason: str | None = None


def _item(rec: DomainRecommendation) -> dict[str, Any]:
    out = Recommendation.from_domain(rec).model_dump(mode="json")
    return {
        "recommendation_id": out["id"],
        "kind": out["kind"],
        "lane": out["lane"],
        "subject": out["subject"],
        "failed_checks": [
            {"rule": c["rule"], "text": c["text"]} for c in out["checks"] if c["result"] == "FAIL"
        ],
        "before": out["impact"]["before"],
        "after": out["impact"]["after_if_executed"],
    }


def explain_request(
    state: DemoState, run: Run, before: DomainSummary, recs: list[DomainRecommendation]
) -> dict[str, Any]:
    """ExplainRequest for POST ai/v1/explain, built after policy and execution."""
    return {
        "run_id": str(run.id),
        "trigger_label": run.trigger_label,
        "mandate": Mandate.from_domain(state.active_mandate).model_dump(mode="json"),
        "forecast_before": ForecastSummary.from_domain(before).model_dump(mode="json"),
        "forecast_after": ForecastSummary.from_domain(state.forecast().summary).model_dump(
            mode="json"
        ),
        "items": [_item(r) for r in recs],
        "findings": [f.model_dump(mode="json") for f in findings(state)],
        "language": "en",
    }


def settle(state: DemoState, run_id: UUID, result: ExplainResult | None, now: datetime) -> None:
    """Store the texts and finish a FINALIZING run; without a result the templates stay."""
    run = state.runs.get(run_id)
    if run is None or run.status is not RunStatus.FINALIZING:
        return
    headline, source = run.headline, run.headline_source
    if result is not None:
        headline, source = result.headline, result.source
        texts = {t.recommendation_id: t.text for t in result.items}
        for rec_id in run.recommendation_ids:
            rec = state.recommendations.get(rec_id)
            if rec is not None and rec_id in texts:
                explanation = {"text": texts[rec_id], "source": result.source}
                state.recommendations[rec_id] = replace(
                    rec, details=rec.details | {"explanation": explanation}
                )
    state.runs[run_id] = replace(
        run,
        status=RunStatus.SUCCEEDED,
        finished_at=now,
        headline=headline,
        headline_source=source,
        explain_request=None,
    )
