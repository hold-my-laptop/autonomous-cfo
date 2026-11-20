"""The agent loop (contract section 3.4, step 2-3).

The model chooses backend tools; the backend runs them and writes the trace. The model
finishes by calling the local `submit_proposals` tool. Proposals are checked against
what the backend's tools returned in this run before they are sent; the backend checks
them again. On any failure the backend is told via /fail and runs FALLBACK itself.
"""

import asyncio
import json
import logging
from typing import Any

from .backend_client import Backend, BackendUnavailable
from .config import Settings
from .llm import LLM
from .prompts import AGENT_SYSTEM, AGENT_USER, SUBMIT_TOOL
from .schemas import AgentCompletion, AgentRunStart, Proposal, ToolResult, ToolSpec

log = logging.getLogger("ai.agent")

STOP_CODES = {"STALE_RUN", "RUN_CLOSED"}
MAX_CORRECTIONS = 2


class AgentFailed(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


class RunStopped(Exception):
    """The backend closed the run or its data changed: stop quietly."""


class RunFacts:
    """What the backend's tools said in this run; proposals must match it."""

    def __init__(self) -> None:
        self.candidates: set[tuple[str, str]] = set()
        self.credit_lines: list[tuple[str, str]] = []

    def record(self, tool: str, result: ToolResult) -> None:
        if not result.ok or not isinstance(result.result, dict):
            return
        r = result.result
        if tool == "find_reschedule_options":
            for c in r.get("candidates") or []:
                if c.get("outflow_id") and c.get("to_date"):
                    self.candidates.add((str(c["outflow_id"]), str(c["to_date"])))
        elif tool == "size_credit_line" and r.get("needed") and r.get("credit_line"):
            cl = r["credit_line"]
            self.credit_lines.append((str(cl.get("amount")), str(cl.get("available_from"))))

    def check(self, raw: Any) -> tuple[list[Proposal] | None, str]:
        if not isinstance(raw, dict) or not isinstance(raw.get("proposals"), list):
            return None, "submit_proposals needs {'proposals': [...]}."
        proposals: list[Proposal] = []
        for i, p in enumerate(raw["proposals"]):
            try:
                prop = Proposal.model_validate(p)
            except Exception as e:  # noqa: BLE001 - message goes back to the model
                return None, f"Proposal {i + 1} is malformed: {e}"
            if prop.kind == "RESCHEDULE_PAYMENT":
                if not prop.move or (prop.move.outflow_id, prop.move.to_date) not in self.candidates:
                    return None, (f"Proposal {i + 1}: the move must be copied exactly from a candidate of "
                                  "find_reschedule_options in this run (outflow_id and to_date).")
            else:
                if prop.credit_line:
                    prop.credit_line.amount = str(prop.credit_line.amount).strip()
                if not self.credit_lines or not prop.credit_line:
                    return None, f"Proposal {i + 1}: call size_credit_line before proposing a credit line."
                if (prop.credit_line.amount, prop.credit_line.available_from) != self.credit_lines[-1]:
                    amount, start = self.credit_lines[-1]
                    return None, (f"Proposal {i + 1}: the credit line must be exactly amount '{amount}' "
                                  f"(cents) from '{start}', as in your last size_credit_line result.")
            proposals.append(prop)
        return proposals, ""


def _openai_tool(spec: ToolSpec) -> dict[str, Any]:
    return {"type": "function",
            "function": {"name": spec.name, "description": spec.description, "parameters": spec.input_schema}}


async def run_agent(start: AgentRunStart, llm: LLM, backend: Backend, settings: Settings) -> str:
    """Runs one agent run end to end. Returns "COMPLETED", "FAILED" or "STOPPED"."""
    run_id = start.run_id
    try:
        completion = await asyncio.wait_for(
            _loop(start, llm, backend, settings), timeout=max(1, start.limits.deadline_s)
        )
    except RunStopped as e:
        log.info("run %s stopped: %s", run_id, e)
        return "STOPPED"
    except (AgentFailed, TimeoutError, BackendUnavailable, Exception) as e:  # noqa: BLE001
        if isinstance(e, AgentFailed):
            reason, detail = e.reason, e.detail
        elif isinstance(e, TimeoutError):
            reason, detail = "MODEL_TIMEOUT", f"deadline of {start.limits.deadline_s}s passed"
        else:
            reason, detail = "MODEL_ERROR", f"{type(e).__name__}: {e}"
        log.warning("run %s FAILED (%s): %s -> backend runs FALLBACK", run_id, reason, detail)
        try:
            await backend.fail(run_id, reason, detail)
        except Exception as e2:  # noqa: BLE001 - backend's deadline will trigger FALLBACK anyway
            log.error("run %s: could not report failure: %s", run_id, e2)
        return "FAILED"
    await backend.complete(run_id, completion)
    log.info("run %s COMPLETED in AGENT mode: %d proposals, %d tool calls",
             run_id, len(completion.proposals), completion.tool_calls)
    return "COMPLETED"


async def _loop(start: AgentRunStart, llm: LLM, backend: Backend, settings: Settings) -> AgentCompletion:
    run_id, max_calls = start.run_id, start.limits.max_tool_calls
    specs = await backend.list_tools()
    tools = [_openai_tool(s) for s in specs] + [SUBMIT_TOOL]
    known = {s.name for s in specs}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": AGENT_USER.format(
            event_label=start.event.label, event_type=start.event.type,
            company_name=start.company.name, as_of_date=start.company.as_of_date,
            currency=start.company.currency, payload=json.dumps(start.event.payload),
            max_tool_calls=max_calls)},
    ]
    facts, used, corrections = RunFacts(), 0, 0
    model = await llm.model_name()

    for _ in range(max_calls + 2 * MAX_CORRECTIONS + 2):
        offered = tools if used < max_calls else [SUBMIT_TOOL]
        try:
            reply = await asyncio.wait_for(llm.chat(messages, offered), timeout=settings.llm_timeout_s)
        except TimeoutError as e:
            raise AgentFailed("MODEL_TIMEOUT", f"one model call took over {settings.llm_timeout_s}s") from e
        except Exception as e:  # noqa: BLE001
            raise AgentFailed("MODEL_ERROR", f"{type(e).__name__}: {e}") from e

        if not reply.tool_calls:
            corrections += 1
            if corrections > MAX_CORRECTIONS:
                raise AgentFailed("NO_FINAL_ANSWER", (reply.content or "")[:200])
            messages.append({"role": "assistant", "content": reply.content or ""})
            messages.append({"role": "user", "content": "Use the tools, then finish by calling submit_proposals."})
            continue

        if reply.content and reply.content.strip():
            await backend.note(run_id, reply.content.strip())
        messages.append({"role": "assistant", "content": reply.content or "", "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in reply.tool_calls]})

        for tc in reply.tool_calls:
            try:
                args = json.loads(tc.arguments or "{}")
            except json.JSONDecodeError:
                args = None

            if tc.name == "submit_proposals":
                proposals, problem = facts.check(args)
                if proposals is not None:
                    note = (args or {}).get("summary_note")
                    return AgentCompletion(proposals=proposals, summary_note=note, model=model,
                                           prompt_version=settings.prompt_version, tool_calls=used)
                corrections += 1
                if corrections > MAX_CORRECTIONS:
                    raise AgentFailed("INVALID_TOOL_CALLS", problem)
                _tool_msg(messages, tc.id, {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": problem}})
                continue

            if tc.name not in known or not isinstance(args, dict):
                corrections += 1
                if corrections > MAX_CORRECTIONS:
                    raise AgentFailed("INVALID_TOOL_CALLS", f"bad call to {tc.name}")
                _tool_msg(messages, tc.id, {"ok": False, "error": {
                    "code": "UNKNOWN_TOOL" if tc.name not in known else "INVALID_ARGUMENT",
                    "message": "Unknown tool." if tc.name not in known else "Arguments must be a JSON object."}})
                continue

            if used >= max_calls:
                _tool_msg(messages, tc.id, {"ok": False, "error": {
                    "code": "TOOL_LIMIT_REACHED", "message": "No tool calls left. Call submit_proposals now."}})
                continue

            result = await backend.call_tool(tc.name, run_id, args, call_id=tc.id)
            used = result.tool_calls_used if result.tool_calls_used is not None else used + 1
            if result.error and result.error.code in STOP_CODES:
                raise RunStopped(result.error.code)
            if result.error and result.error.code == "TOOL_LIMIT_REACHED":
                used = max_calls
            facts.record(tc.name, result)
            _tool_msg(messages, tc.id, result.model_dump(exclude_none=True))

    raise AgentFailed("TOOL_LIMIT_REACHED" if used >= max_calls else "NO_FINAL_ANSWER",
                      "the model did not submit proposals")


def _tool_msg(messages: list[dict[str, Any]], call_id: str, body: dict[str, Any]) -> None:
    messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(body, ensure_ascii=False)})
