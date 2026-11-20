from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from api.deps import AgentClientDep, RepositoryDep, SettingsDep
from api.schemas import ModelHealth
from api.state import StateNotLoadedError

router = APIRouter(prefix="/health", tags=["health"])


class HealthStatus(BaseModel):
    status: Literal["ok"]


class ReadyStatus(BaseModel):
    status: Literal["ok", "unavailable"]
    state_loaded: bool


@router.get("/live")
def health_live() -> HealthStatus:
    """The process is up. No dependencies are checked (used by liveness probes)."""
    return HealthStatus(status="ok")


@router.get(
    "/ready",
    responses={503: {"model": ReadyStatus, "description": "The demo state is not loaded"}},
)
def health_ready(repository: RepositoryDep, response: Response) -> ReadyStatus:
    """The demo state is loaded and requests can be served (used by readiness probes)."""
    try:
        with repository.read():
            return ReadyStatus(status="ok", state_loaded=True)
    except StateNotLoadedError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyStatus(status="unavailable", state_loaded=False)


@router.get(
    "/model",
    responses={503: {"model": ModelHealth, "description": "The model cannot be reached"}},
)
def health_model(client: AgentClientDep, settings: SettingsDep, response: Response) -> ModelHealth:
    """Whether the LLM served on OpenShift AI answers, as checked by the AI service.

    503 with ok=false when the model, the AI service or agent mode (INTERNAL_TOKEN) is missing."""
    if not settings.agent_enabled:
        result = ModelHealth(
            ok=False,
            model="unknown",
            mode=None,
            latency_ms=0,
            error="Agent mode is off (INTERNAL_TOKEN not set)",
        )
    else:
        result = client.model_health()
    if not result.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
