"""Run-scoped forecast access for the AI service."""
from secrets import compare_digest
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from api.deps import RepositoryDep, SettingsDep
from api.domain.models import RunStatus
from api.idempotency import request_hash, run_idempotent
from api.schemas import Forecast


def require_token(settings: SettingsDep, authorization: str | None = Header(None)) -> None:
    secret = settings.internal_token
    if not secret or not secret.get_secret_value():
        raise HTTPException(503, "Internal authentication is not configured")
    expected = f"Bearer {secret.get_secret_value()}".encode()
    if not compare_digest((authorization or "").encode(), expected):
        raise HTTPException(401, "Invalid internal credentials")


router = APIRouter(prefix="/internal/v1", dependencies=[Depends(require_token)])
TOOL = {
    "name": "get_forecast",
    "description": "Read the current cash forecast summary, drivers and planned events.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}


class ToolCall(BaseModel):
    run_id: UUID
    call_id: str = Field(min_length=1, max_length=256)
    arguments: dict[str, Any]


@router.get("/tools")
def list_tools():
    return [TOOL]


@router.post("/tools/{name}")
def call_tool(name: str, body: ToolCall, repository: RepositoryDep):
    with repository.write() as state:
        prefix = f"tool:{body.run_id}:"
        used = sum(key.startswith(prefix) for key in state.idempotency)

        def error(code, message):
            return {"ok": False, "error": {"code": code, "message": message},
                    "financial_version": state.financial_version, "tool_calls_used": used}

        run = state.runs.get(body.run_id)
        if run is None or run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
            return error("RUN_CLOSED", "An active run is required")
        event = next((event for event in state.events if event.id == run.event_id), None)
        if event is None or event.financial_version_after != state.financial_version:
            return error("STALE_RUN", "Financial data changed since the run started")
        if name != TOOL["name"]:
            return error("UNKNOWN_TOOL", "Unknown tool")
        if body.arguments:
            return error("INVALID_ARGUMENT", "get_forecast takes no arguments")

        def produce():
            forecast = Forecast.from_domain(state.forecast(), state)
            return {"ok": True, "financial_version": state.financial_version,
                    "tool_calls_used": used + 1,
                    "result": forecast.model_dump(mode="json", include={"summary", "drivers", "planned_events"})}

        return run_idempotent(state, prefix + body.call_id, request_hash("POST", name, body), produce)
