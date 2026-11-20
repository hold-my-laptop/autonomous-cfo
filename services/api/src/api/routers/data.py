from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from api.deps import RepositoryDep
from api.domain.models import Category
from api.schemas import Company, Inflow, Outflow, PlannedEvent, RevenueModel, Transaction

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
