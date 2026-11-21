import hmac
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.agent import RunClosedError
from api.deps import AgentDep, ProofDep, RepositoryDep, SettingsDep
from api.domain.models import RunKind
from api.domain.proof.findings import Classification
from api.errors import ApiError, ErrorCode
from api.money import Money
from api.tools import call_tool, tool_specs


def require_internal_token(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    """Only the AI service (holding INTERNAL_TOKEN) may call /internal."""
    expected = settings.internal_token.get_secret_value() if settings.internal_token else None
    scheme, _, token = (authorization or "").partition(" ")
    if (
        not expected
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(token.encode(), expected.encode())
    ):
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
def list_tools(repository: RepositoryDep, run_id: UUID | None = None) -> list[ToolSpec]:
    """The tools of that run's kind; without `run_id`, the Cash tools."""
    kind = RunKind.CASH
    if run_id is not None:
        with repository.read() as state:
            run = state.runs.get(run_id)
        if run is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, f"no run {run_id}")
        kind = run.kind
    return [ToolSpec(**spec) for spec in tool_specs(kind)]


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


class MoveIn(BaseModel):
    outflow_id: UUID
    to_date: date


class CreditLineIn(BaseModel):
    amount: Money
    available_from: date


class ProposalIn(BaseModel):
    """Shape only; whether a proposal is usable (known kind, a planner candidate, the sized
    credit line) is decided when the run is finalized, and unusable ones are discarded."""

    kind: str = Field(max_length=50)
    move: MoveIn | None = None
    credit_line: CreditLineIn | None = None
    rationale: str | None = Field(default=None, max_length=1000)


class CompletionIn(BaseModel):
    """The agent's final answer; proposals are re-checked against this run's tool results."""

    proposals: list[ProposalIn] = Field(max_length=10)
    summary_note: str | None = Field(default=None, max_length=2000)
    model: str | None = None
    prompt_version: str | None = None
    tool_calls: int | None = None


class InvestigationCompletionIn(BaseModel):
    """A Proof investigation's answer (`submit_finding`). The label is only a proposal: the
    backend checks the citations and the `compute` call it names, and sets the label."""

    model_config = ConfigDict(extra="forbid")

    classification: Classification
    compute_call_id: str | None = Field(default=None, max_length=200)
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )
    explanation: str = Field(min_length=1, max_length=2000)
    letter_draft: str | None = Field(default=None, max_length=5000)
    model: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=200)
    tool_calls: int | None = Field(default=None, ge=0)


class FailureIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=500)


def _closed(exc: RunClosedError) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, ErrorCode.RUN_CLOSED, str(exc))


def _kind(repository, run_id: UUID) -> RunKind:
    """A callback goes to the orchestrator of its run's kind; an unknown run is answered by
    the Cash orchestrator with RUN_CLOSED."""
    with repository.read() as state:
        run = state.runs.get(run_id)
    return run.kind if run else RunKind.CASH


def _parse[M: BaseModel](model: type[M], body: dict[str, Any]) -> M:
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise RequestValidationError(
            [e | {"loc": ("body", *e["loc"])} for e in exc.errors(include_url=False)]
        ) from exc


@router.post("/runs/{run_id}/notes", status_code=status.HTTP_204_NO_CONTENT)
def add_note(
    run_id: UUID, body: NoteIn, agent: AgentDep, proof: ProofDep, repository: RepositoryDep
) -> None:
    try:
        if _kind(repository, run_id) is RunKind.PROOF:
            proof.note(run_id, body.text)
        else:
            agent.note(run_id, body.text)
    except RunClosedError as exc:
        raise _closed(exc) from exc


@router.post("/runs/{run_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
def complete_run(
    run_id: UUID,
    body: Annotated[dict[str, Any], Body()],
    agent: AgentDep,
    proof: ProofDep,
    repository: RepositoryDep,
    background: BackgroundTasks,
) -> None:
    """A Cash run sends CompletionIn, a Proof investigation InvestigationCompletionIn; the
    wrong shape for the run's kind is a 422. A Cash run's texts are asked for after the
    response, so the agent is not kept waiting."""
    try:
        if _kind(repository, run_id) is RunKind.PROOF:
            answer = _parse(InvestigationCompletionIn, body)
            proof.complete(run_id, answer.model_dump(mode="json"))
            return
        completion = _parse(CompletionIn, body)
        agent.complete(run_id, completion.model_dump(mode="json"))
    except RunClosedError as exc:
        raise _closed(exc) from exc
    background.add_task(agent.explain, run_id)


@router.post("/runs/{run_id}/fail", status_code=status.HTTP_204_NO_CONTENT)
def fail_run(
    run_id: UUID,
    body: FailureIn,
    agent: AgentDep,
    proof: ProofDep,
    repository: RepositoryDep,
    background: BackgroundTasks,
) -> None:
    try:
        if _kind(repository, run_id) is RunKind.PROOF:
            proof.fail(run_id, body.reason)
            return
        agent.fail(run_id, body.reason)
    except RunClosedError as exc:
        raise _closed(exc) from exc
    background.add_task(agent.explain, run_id)
