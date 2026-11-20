from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from api.deps import RepositoryDep
from api.state import StateNotLoadedError

router = APIRouter(prefix="/health", tags=["health"])


class HealthStatus(BaseModel):
    status: Literal["ok"]


class ReadyStatus(BaseModel):
    status: Literal["ok", "unavailable"]
    state_loaded: bool


@router.get("/live")
def live() -> HealthStatus:
    """The process is up. No dependencies are checked (used by liveness probes)."""
    return HealthStatus(status="ok")


@router.get("/ready")
def ready(repository: RepositoryDep, response: Response) -> ReadyStatus:
    """The demo state is loaded and requests can be served (used by readiness probes)."""
    try:
        with repository.read():
            return ReadyStatus(status="ok", state_loaded=True)
    except StateNotLoadedError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyStatus(status="unavailable", state_loaded=False)
