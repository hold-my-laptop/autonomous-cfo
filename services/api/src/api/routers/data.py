from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from api.commands import request_outflow_reschedule
from api.deps import RepositoryDep
from api.domain.models import Category
from api.idempotency import IdempotencyKeyDep, request_hash, run_idempotent
from api.schemas import (
    Company,
    Inflow,
    Outflow,
    PlannedEvent,
    RescheduleIn,
    RevenueModel,
    RunAccepted,
    Transaction,
)

router = APIRouter(tags=["data"])


@router.get("/company")
def get_company(repository: RepositoryDep) -> Company:
    with repository.read() as state:
        return Company.from_state(state)


@router.get("/transactions")
def list_transactions(
    repository: RepositoryDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    category: Category | None = None,
) -> list[Transaction]:
    """Statement lines the owner already has (booked before the demo's "today"), oldest first."""
    with repository.read() as state:
        return [
            Transaction.from_domain(t)
            for t in state.history
            if (date_from is None or t.booking_date >= date_from)
            and (date_to is None or t.booking_date <= date_to)
            and (category is None or t.category is category)
        ]


@router.get("/outflows")
def list_outflows(repository: RepositoryDep) -> list[Outflow]:
    with repository.read() as state:
        mandate = state.active_mandate
        return [Outflow.from_domain(o, mandate) for o in state.outflows]


@router.get("/inflows")
def list_inflows(repository: RepositoryDep) -> list[Inflow]:
    with repository.read() as state:
        return [Inflow.from_domain(i) for i in state.inflows]


@router.get("/revenue-model")
def get_revenue_model(repository: RepositoryDep) -> RevenueModel:
    with repository.read() as state:
        return RevenueModel.from_domain(state.revenue_model)


@router.get("/planned-events")
def list_planned_events(repository: RepositoryDep) -> list[PlannedEvent]:
    with repository.read() as state:
        return [PlannedEvent.from_domain(e) for e in state.planned_events]


@router.post("/outflows/{outflow_id}/reschedule-requests", status_code=status.HTTP_202_ACCEPTED)
def request_reschedule(
    outflow_id: UUID,
    body: RescheduleIn,
    key: IdempotencyKeyDep,
    repository: RepositoryDep,
    request: Request,
) -> RunAccepted:
    """The owner asks to move a payment. The same rules apply; protected payments are refused."""
    with repository.write() as state:

        def apply() -> RunAccepted:
            run = request_outflow_reschedule(
                state, outflow_id, body.to_date, body.note, datetime.now(UTC)
            )
            return RunAccepted(run_id=run.id, status=run.status)

        return run_idempotent(state, key, request_hash("POST", request.url.path, body), apply)
