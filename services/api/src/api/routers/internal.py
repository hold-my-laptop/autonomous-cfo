import hmac
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field

from api.agent import RunClosedError
from api.deps import AgentDep, RepositoryDep, SettingsDep
from api.errors import ApiError, ErrorCode
from api.tools import call_tool, tool_specs


def require_internal_token(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    """Only the AI service (holding INTERNAL_TOKEN) may call /internal."""
    expected = settings.internal_token.get_secret_value() if settings.internal_token else None
    scheme, _, token = (authorization or "").partition(" ")
    if not expected or scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, ErrorCode.UNAUTHORIZED)


router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_internal_token)],
)


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = Field(min_length=1, max_length=200)


class ToolErrorOut(BaseModel):
    code: str
    message: str


class ToolResult(BaseModel):
    ok: bool
    result: Any
    error: ToolErrorOut | None
    financial_version: int
    tool_calls_used: int


@router.get("/tools")
def list_tools() -> list[ToolSpec]:
    return [ToolSpec(**spec) for spec in tool_specs()]


@router.post("/tools/{name}")
def run_tool(
    name: str, body: ToolCall, repository: RepositoryDep, settings: SettingsDep
) -> ToolResult:
    """Tool errors come back as ok=false with a code, not as HTTP errors."""
    with repository.write() as state:
        response = call_tool(
            state, settings, name, body.run_id, body.call_id, body.arguments, datetime.now(UTC)
        )
    return ToolResult(**response)


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class CompletionIn(BaseModel):
    """The agent's final answer; proposals are re-checked against this run's tool results."""

    proposals: list[dict[str, Any]] = Field(max_length=10)
    summary_note: str | None = Field(default=None, max_length=2000)
    model: str | None = None
    prompt_version: str | None = None
    tool_calls: int | None = None


class FailureIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=500)


def _closed(exc: RunClosedError) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, ErrorCode.RUN_CLOSED, str(exc))


@router.post("/runs/{run_id}/notes", status_code=status.HTTP_204_NO_CONTENT)
def add_note(run_id: UUID, body: NoteIn, agent: AgentDep) -> None:
    try:
        agent.note(run_id, body.text)
    except RunClosedError as exc:
        raise _closed(exc) from exc


@router.post("/runs/{run_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
def complete_run(
    run_id: UUID, body: CompletionIn, agent: AgentDep, background: BackgroundTasks
) -> None:
    """The texts are asked for after the response, so the agent is not kept waiting."""
    try:
        agent.complete(run_id, body.model_dump())
    except RunClosedError as exc:
        raise _closed(exc) from exc
    background.add_task(agent.explain, run_id)


@router.post("/runs/{run_id}/fail", status_code=status.HTTP_204_NO_CONTENT)
def fail_run(run_id: UUID, body: FailureIn, agent: AgentDep, background: BackgroundTasks) -> None:
    try:
        agent.fail(run_id, body.reason)
    except RunClosedError as exc:
        raise _closed(exc) from exc
    background.add_task(agent.explain, run_id)
