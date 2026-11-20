from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from api.deps import RepositoryDep
from api.errors import ApiError, ErrorCode
from api.overview import build_overview
from api.schemas import Overview, Run

router = APIRouter(tags=["runs"])


@router.get("/runs")
def list_runs(
    repository: RepositoryDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[Run]:
    """Newest first."""
    with repository.read() as state:
        runs = sorted(state.runs.values(), key=lambda r: r.created_at, reverse=True)[:limit]
        return [Run.from_domain(r, state) for r in runs]


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, repository: RepositoryDep) -> Run:
    with repository.read() as state:
        run = state.runs.get(run_id)
        if run is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, f"no run {run_id}")
        return Run.from_domain(run, state)


@router.get("/overview")
def get_overview(repository: RepositoryDep) -> Overview:
    """Everything the main screen needs in one call."""
    with repository.read() as state:
        return build_overview(state)
