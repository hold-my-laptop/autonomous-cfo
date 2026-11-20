from uuid import UUID

from fastapi import APIRouter, status

from api.deps import RepositoryDep
from api.errors import ApiError, ErrorCode
from api.schemas import Run

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("/{run_id}")
def get_run(run_id: UUID, repository: RepositoryDep) -> Run:
    with repository.read() as state:
        run = state.runs.get(run_id)
        if run is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, f"no run {run_id}")
        return Run.from_domain(run)
