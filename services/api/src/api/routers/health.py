from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["health"])


class HealthStatus(BaseModel):
    status: Literal["ok"]


@router.get("/live")
def live() -> HealthStatus:
    """The process is up. No dependencies are checked (used by liveness probes)."""
    return HealthStatus(status="ok")
