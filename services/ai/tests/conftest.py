import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.config import Settings  # noqa: E402
from ai.llm import LLMReply, ToolCallOut  # noqa: E402
from ai.schemas import ToolResult, ToolSpec  # noqa: E402

OUTFLOW = "11111111-1111-1111-1111-111111111111"
CANDIDATE = {"outflow_id": OUTFLOW, "ref": "CHF-06", "counterparty": "Luigi Amalfi", "amount": "437500",
             "from_date": "2025-06-15", "to_date": "2025-07-30"}
CREDIT = {"amount": "2000000", "available_from": "2025-06-28", "available_until": "2025-09-30",
          "shortfall": "1712345", "rounded_to": "500000", "headroom_at_lowest": "287655"}


class FakeBackend:
    """In-memory stand-in for the backend's /internal/v1 API."""

    def __init__(self, breach: bool = True):
        self.breach = breach
        self.calls: list[tuple[str, dict]] = []
        self.notes: list[str] = []
        self.completed = None
        self.failed = None
        self.stale_after: int | None = None

    async def list_tools(self):
        return [ToolSpec(name=n, description=f"{n} tool", input_schema={"type": "object", "properties": {}})
                for n in ("get_mandate", "get_forecast", "find_reschedule_options", "simulate_moves",
                          "size_credit_line", "get_fx_exposure", "check_policy")]

    async def call_tool(self, name, run_id, arguments, call_id):
        self.calls.append((name, arguments))
        used = len(self.calls)
        if self.stale_after is not None and used > self.stale_after:
            return ToolResult(ok=False, error={"code": "STALE_RUN", "message": "data changed"}, tool_calls_used=used)
        if used > 8:
            return ToolResult(ok=False, error={"code": "TOOL_LIMIT_REACHED"}, tool_calls_used=used)
        results = {
            "get_forecast": {"summary": {"first_breach": {"date": "2025-06-28", "closing": "-2212345",
                                                          "shortfall": "1712345"} if self.breach else None,
                                         "lowest": {"date": "2025-07-03", "closing": "-2209760"}}},
            "find_reschedule_options": {"candidates": [CANDIDATE], "resolved": False},
            "size_credit_line": {"needed": True, "credit_line": CREDIT},
        }
        return ToolResult(ok=True, result=results.get(name, {}), financial_version=3, tool_calls_used=used)

    async def note(self, run_id, text):
        self.notes.append(text)

    async def complete(self, run_id, completion):
        self.completed = completion

    async def fail(self, run_id, reason, detail=None):
        self.failed = (reason, detail)


class ScriptedLLM:
    """Plays back a fixed list of replies; each item is (tool_name, args) or a plain string."""

    mode = "live"

    def __init__(self, script):
        self.script = list(script)
        self.seen_messages = []

    async def model_name(self):
        return "scripted-model"

    async def chat(self, messages, tools=None, *, json_schema=None, max_tokens=None):
        self.seen_messages.append(list(messages))
        if not self.script:
            return LLMReply(content="I am done talking.")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, str):
            return LLMReply(content=step)
        name, args = step
        return LLMReply(content=None, tool_calls=[ToolCallOut(id=f"c{len(self.seen_messages)}", name=name,
                                                              arguments=json.dumps(args))])


@pytest.fixture
def settings():
    return Settings(llm_mode="live", llm_base_url="http://unused/v1", llm_timeout_s=5)


@pytest.fixture
def backend():
    return FakeBackend()
