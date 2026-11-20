from datetime import UTC, datetime

from fastapi import APIRouter, Request, status

from api.commands import plan_closure
from api.deps import RepositoryDep
from api.idempotency import IdempotencyKeyDep, check_financial_version, request_hash, run_idempotent
from api.schemas import EventIn, RunAccepted

router = APIRouter(prefix="/events", tags=["runs"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def post_event(
    body: EventIn, key: IdempotencyKeyDep, repository: RepositoryDep, request: Request
) -> RunAccepted:
    """Tell Vero something changed. Starts a run; poll GET /runs/{run_id}."""
    with repository.write() as state:

        def apply() -> RunAccepted:
            check_financial_version(state, body.expected_financial_version)
            run = plan_closure(
                state,
                body.payload.date_from,
                body.payload.date_to,
                body.payload.variable_costs_stop,
                body.payload.note,
                datetime.now(UTC),
            )
            return RunAccepted(run_id=run.id, status=run.status)

        return run_idempotent(state, key, request_hash("POST", request.url.path, body), apply)
