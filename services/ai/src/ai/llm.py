"""The only place that talks to the language model.

`LiveLLM` calls the model served by vLLM on OpenShift AI through its OpenAI-compatible
API (the `openai` package is only the HTTP client; nothing goes to api.openai.com).
`StubLLM` is a deterministic stand-in for local development and tests. It must never
be used in the demo: runs it drives are not real AGENT runs.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from openai import AsyncOpenAI

from .config import Settings

log = logging.getLogger("ai.llm")


@dataclass
class ToolCallOut:
    id: str
    name: str
    arguments: str  # JSON text as the model wrote it


@dataclass
class LLMReply:
    content: str | None
    tool_calls: list[ToolCallOut] = field(default_factory=list)


class LLM(Protocol):
    mode: str

    async def model_name(self) -> str: ...

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> LLMReply: ...


class LiveLLM:
    mode = "live"

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None):
        if not settings.llm_base_url:
            # Without a base URL the SDK would default to api.openai.com. Refuse instead.
            raise RuntimeError("LLM_BASE_URL is not set (live mode). Refusing to start.")
        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_s,
            max_retries=0,
            http_client=http_client,
        )
        self._model = settings.llm_model or None

    async def model_name(self) -> str:
        if not self._model:
            models = await self._client.models.list()
            if not models.data:
                raise RuntimeError("The model server lists no models")
            self._model = models.data[0].id
        return self._model

    async def chat(self, messages, tools=None, *, json_schema=None, max_tokens=None) -> LLMReply:
        kwargs: dict[str, Any] = {
            "model": await self.model_name(),
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens or self._settings.llm_max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": json_schema},
            }
        resp = await self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = [
            ToolCallOut(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
        ]
        return LLMReply(content=msg.content, tool_calls=calls)


class StubLLM:
    """Deterministic agent for development: forecast -> planner -> credit line -> submit."""

    mode = "stub"
    ORDER = ("get_forecast", "find_reschedule_options", "size_credit_line")

    async def model_name(self) -> str:
        return "stub"

    async def chat(self, messages, tools=None, *, json_schema=None, max_tokens=None) -> LLMReply:
        if json_schema:  # explain: let the template path do the work
            return LLMReply(content="{}")
        offered = {t["function"]["name"] for t in (tools or [])}
        names_by_id: dict[str, str] = {}
        results: dict[str, Any] = {}
        for m in messages:
            for tc in m.get("tool_calls") or []:
                names_by_id[tc["id"]] = tc["function"]["name"]
            if m.get("role") == "tool":
                try:
                    results[names_by_id.get(m["tool_call_id"], "?")] = json.loads(m["content"])
                except (KeyError, json.JSONDecodeError):
                    pass
        first_move = None
        plan = (results.get("find_reschedule_options") or {}).get("result") or {}
        if plan.get("candidates"):
            c = plan["candidates"][0]
            first_move = {"outflow_id": c["outflow_id"], "to_date": c["to_date"]}
        for name in self.ORDER:
            if name in offered and name not in results:
                args: dict[str, Any] = {}
                if name == "size_credit_line" and first_move:
                    args = {"after_moves": [first_move]}
                return LLMReply(None, [ToolCallOut(f"stub-{name}", name, json.dumps(args))])
        proposals: list[dict[str, Any]] = []
        if first_move:
            proposals.append({"kind": "RESCHEDULE_PAYMENT", "move": first_move,
                              "rationale": "Moving this payment delays the shortfall."})
        credit = (results.get("size_credit_line") or {}).get("result") or {}
        if credit.get("needed") and credit.get("credit_line"):
            cl = credit["credit_line"]
            proposals.append({"kind": "CREDIT_LINE",
                              "credit_line": {"amount": cl["amount"], "available_from": cl["available_from"]},
                              "rationale": "Covers the gap that remains."})
        args = {"proposals": proposals, "summary_note": "Stub agent (not a model)."}
        return LLMReply(None, [ToolCallOut("stub-submit", "submit_proposals", json.dumps(args))])


def build_llm(settings: Settings) -> LLM:
    if settings.llm_mode == "stub":
        log.warning("LLM_MODE=stub: runs are NOT driven by a model. Never use this in the demo.")
        return StubLLM()
    return LiveLLM(settings)
