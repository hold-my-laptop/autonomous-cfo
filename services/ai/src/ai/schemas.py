"""Types from docs/contracts/DATA_CONTRACTS.md, section 3 (Backend <-> AI).
Money is a string with an integer number of cents. Unknown fields are kept, so the
backend can add fields without breaking this service."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class RunEvent(_Open):
    type: str
    label: str = ""
    payload: dict[str, Any] = {}


class RunCompany(_Open):
    name: str = ""
    as_of_date: str = ""
    currency: str = "EUR"


class RunLimits(_Open):
    max_tool_calls: int = 8
    deadline_s: int = 60


class AgentRunStart(_Open):
    run_id: str
    event: RunEvent
    company: RunCompany = RunCompany()
    limits: RunLimits = RunLimits()


class ToolSpec(_Open):
    name: str
    description: str
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}


class ToolError(_Open):
    code: str
    message: str = ""


class ToolResult(_Open):
    ok: bool
    result: Any = None
    error: ToolError | None = None
    financial_version: int | None = None
    tool_calls_used: int | None = None


class Move(_Open):
    outflow_id: str
    to_date: str


class CreditLineProposal(_Open):
    amount: str
    available_from: str


class Proposal(_Open):
    kind: Literal["RESCHEDULE_PAYMENT", "CREDIT_LINE"]
    move: Move | None = None
    credit_line: CreditLineProposal | None = None
    rationale: str | None = None


class AgentCompletion(_Open):
    proposals: list[Proposal]
    summary_note: str | None = None
    model: str
    prompt_version: str
    tool_calls: int


FailureReason = Literal[
    "MODEL_TIMEOUT", "MODEL_ERROR", "INVALID_TOOL_CALLS", "NO_FINAL_ANSWER", "TOOL_LIMIT_REACHED"
]


class ExplainItem(_Open):
    recommendation_id: str
    kind: str
    lane: str
    subject: dict[str, Any] = {}
    failed_checks: list[dict[str, Any]] = []
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}


class ExplainRequest(_Open):
    run_id: str
    trigger_label: str = ""
    mandate: dict[str, Any] = {}
    forecast_before: dict[str, Any] = {}
    forecast_after: dict[str, Any] = {}
    items: list[ExplainItem] = []
    findings: list[Any] = []
    language: Literal["en", "de"] = "en"


class ExplainText(BaseModel):
    recommendation_id: str
    text: str


class ExplainResult(BaseModel):
    headline: str
    items: list[ExplainText]
    source: Literal["MODEL", "REPLAY", "TEMPLATE"]
    fallback_reason: str | None = None


class ModelHealth(BaseModel):
    ok: bool
    model: str
    mode: Literal["live", "replay", "stub"]
    latency_ms: int
    error: str | None = None
