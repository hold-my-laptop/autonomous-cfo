from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status

from api.decisions import DecisionError, decide
from api.deps import RepositoryDep
from api.domain.models import RecommendationStatus
from api.errors import ApiError, ErrorCode
from api.idempotency import IdempotencyKeyDep, request_hash, run_idempotent
from api.schemas import DecisionIn, DecisionResult, ForecastSummary, Receipt, Recommendation

router = APIRouter(tags=["recommendations"])

_ERRORS = {
    "NOT_FOUND": (status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND),
    "NOT_PENDING": (status.HTTP_409_CONFLICT, ErrorCode.NOT_PENDING),
    "STALE_RECOMMENDATION": (status.HTTP_409_CONFLICT, ErrorCode.STALE_RECOMMENDATION),
}


@router.get("/recommendations")
def list_recommendations(
    repository: RepositoryDep,
    lane: Literal["DONE_WITHIN_MANDATE", "NEEDS_APPROVAL", "NOT_ALLOWED"] | None = None,
    status: RecommendationStatus | None = None,
) -> list[Recommendation]:
    """Newest first."""
    with repository.read() as state:
        return [
            Recommendation.from_domain(r)
            for r in sorted(
                state.recommendations.values(), key=lambda r: r.created_at, reverse=True
            )
            if (lane is None or r.lane == lane) and (status is None or r.status is status)
        ]


@router.get("/recommendations/{recommendation_id}")
def get_recommendation(recommendation_id: UUID, repository: RepositoryDep) -> Recommendation:
    with repository.read() as state:
        rec = state.recommendations.get(recommendation_id)
        if rec is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no recommendation {recommendation_id}")
        return Recommendation.from_domain(rec)


def _decide(
    recommendation_id: UUID,
    approve: bool,
    body: DecisionIn | None,
    key: str,
    repository: RepositoryDep,
    request: Request,
) -> DecisionResult:
    failure: DecisionError | None = None
    with repository.write() as state:

        def apply() -> DecisionResult:
            rec, receipt = decide(
                state, recommendation_id, approve, body.note if body else None, datetime.now(UTC)
            )
            return DecisionResult(
                recommendation=Recommendation.from_domain(rec),
                forecast_summary=ForecastSummary.from_domain(state.forecast().summary),
                receipt_id=receipt.id,
                follow_up_recommendation_ids=[],
            )

        try:
            return run_idempotent(state, key, request_hash("POST", request.url.path, body), apply)
        except DecisionError as error:
            failure = error
    status_code, code = _ERRORS[failure.code]
    raise ApiError(status_code, code, failure.detail)


@router.post("/recommendations/{recommendation_id}/approve")
def approve(
    recommendation_id: UUID,
    key: IdempotencyKeyDep,
    repository: RepositoryDep,
    request: Request,
    body: DecisionIn | None = None,
) -> DecisionResult:
    """Approve a pending recommendation; it is executed in simulation."""
    return _decide(recommendation_id, True, body, key, repository, request)


@router.post("/recommendations/{recommendation_id}/reject")
def reject(
    recommendation_id: UUID,
    key: IdempotencyKeyDep,
    repository: RepositoryDep,
    request: Request,
    body: DecisionIn | None = None,
) -> DecisionResult:
    """Reject a pending recommendation. Nothing changes; the payment keeps its date."""
    return _decide(recommendation_id, False, body, key, repository, request)


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: UUID, repository: RepositoryDep) -> Receipt:
    with repository.read() as state:
        receipt = state.receipts.get(receipt_id)
        if receipt is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no receipt {receipt_id}")
        return Receipt.from_domain(receipt)
