"""The investigation loop for Vero Proof (docs/BACKEND.md P10, docs/PROOF.md §5.5).

Same shape as the Cash agent (agent.py), with its own prompt and its own final tool
`submit_finding`. The model calls the backend's Proof tools (search_inbox, read_clause,
get_reference_index, compute, ...) for one finding and proposes a classification. Before the
answer is sent it is checked against what the tools returned in this run, as the RAG answers are:

- every evidence id must come from a tool result of this run;
- every number in the explanation and the letter must appear in a tool result or the finding;
- CONFLICT and VERIFIED need a successful `compute` call. The call that decides the label is
  sent as `compute_call_id` (the backend recomputes that formula and sets the final label, P11).
  Only vpi_threshold_indexation and surcharge_rule decide a label in the backend, so the call is
  the last one of the formula the model names in `formula_id`, else the last call of either,
  never a helper such as annualize or variance.

On any failure the backend is told via /fail and runs its scripted investigation (P12).
"""

import asyncio
import json
import logging
from typing import Any

from . import tracing
from .agent import (
    MAX_CORRECTIONS,
    STOP_CODES,
    AgentFailed,
    RunStopped,
    _openai_tool,
    _tool_msg,
)
from .backend_client import Backend, BackendUnavailable
from .config import Settings
from .explain import NUMBER, _digits
from .llm import LLM
from .prompts import (
    INVESTIGATION_PROMPT_VERSION,
    INVESTIGATION_SYSTEM,
    INVESTIGATION_USER,
    SUBMIT_FINDING_TOOL,
)
from .schemas import AgentRunStart, InvestigationCompletion, ToolResult

log = logging.getLogger("ai.investigation")

CLASSIFICATIONS = {"VERIFIED", "UNEXPLAINED", "CONFLICT"}
NEEDS_COMPUTE = {"VERIFIED", "CONFLICT"}
DECISIVE_FORMULAS = ("vpi_threshold_indexation", "surcharge_rule")
SMALL_NUMBERS = {str(n) for n in range(11)}  # counts and list markers ("2 emails")
EXPLANATION_LIMIT = 1200
LETTER_LIMIT = 2500
EVIDENCE_LIMIT = 20  # the backend accepts at most 20 citations


def _evidence_ids(value: Any) -> list[str]:
    """Every `evidence_ids` list anywhere in a tool result."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_ids" and isinstance(item, list):
                found += [str(i) for i in item]
            else:
                found += _evidence_ids(item)
    elif isinstance(value, list):
        for item in value:
            found += _evidence_ids(item)
    return found


def _numbers(text: str) -> set[str]:
    """Numbers as digit strings without leading zeros ("01" and "1" are the same)."""
    out = set()
    for token in NUMBER.findall(text or ""):
        digits = _digits(token)
        if digits:
            out.add(str(int(digits)))
    return out


class InvestigationFacts:
    """What the Proof tools returned in this run; the finding must stay inside it."""

    def __init__(self, finding: dict[str, Any], context: dict[str, Any] | None = None) -> None:
        self.evidence: list[str] = []
        self.compute_calls: list[tuple[str, str]] = []  # (call_id, formula_id), in call order
        self._allowed: set[str] = set()
        self._add_numbers(json.dumps(finding, ensure_ascii=False))
        self._add_numbers(json.dumps(context or {}, ensure_ascii=False))

    def _add_numbers(self, text: str) -> None:
        for n in _numbers(text):
            self._allowed.add(n)
            value = int(n)
            if value % 100 == 0:  # 380000 cents -> "3,800", 800 bps -> "8 %"
                self._allowed.add(str(value // 100))

    def record(self, tool: str, call_id: str, result: ToolResult, args: dict[str, Any] | None = None) -> None:
        if not result.ok:
            return
        for evidence in _evidence_ids(result.result):
            if evidence not in self.evidence:
                self.evidence.append(evidence)
        if tool == "compute":
            body = result.result if isinstance(result.result, dict) else {}
            formula = str(body.get("formula_id") or (args or {}).get("formula_id") or "")
            self.compute_calls.append((call_id, formula))
        self._add_numbers(json.dumps(result.result, ensure_ascii=False))

    def deciding_call(self, formula: str | None) -> tuple[str | None, str]:
        """The compute call the label rests on: the named formula's last call, else the last
        call of a deciding formula (index clause, surcharge)."""
        deciding = " or ".join(DECISIVE_FORMULAS)
        if formula and formula not in DECISIVE_FORMULAS:
            return None, f"formula_id must be {deciding}: only these decide a label, not {formula!r}."
        wanted = (formula,) if formula else DECISIVE_FORMULAS
        calls = [c for c, f in self.compute_calls if f in wanted]
        if not calls:
            done = ", ".join(sorted({f for _, f in self.compute_calls})) or "nothing"
            return None, (f"A CONFLICT or VERIFIED label needs a successful compute call with "
                          f"{formula or deciding} (computed so far: {done}).")
        return calls[-1], ""

    def unknown_numbers(self, text: str) -> list[str]:
        return sorted(n for n in _numbers(text) if n not in self._allowed and n not in SMALL_NUMBERS)

    def check(self, raw: Any) -> tuple[dict[str, Any] | None, str]:
        if not isinstance(raw, dict):
            return None, "submit_finding needs a JSON object."
        label = raw.get("classification")
        if label not in CLASSIFICATIONS:
            return None, "classification must be VERIFIED, UNEXPLAINED or CONFLICT."
        refs = raw.get("evidence_refs")
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            return None, "evidence_refs must be a list of evidence ids (strings)."
        unknown = [r for r in refs if r not in self.evidence]
        if unknown:
            allowed = ", ".join(self.evidence) or "none yet"
            return None, (f"These evidence ids were not returned by a tool in this run: {unknown}. "
                          f"Use only: {allowed}.")
        if label in NEEDS_COMPUTE and not self.compute_calls:
            return None, f"{label} needs a successful compute call first. Call compute, then submit."
        if label in NEEDS_COMPUTE and not refs:
            return None, f"{label} needs evidence_refs: the ids of the documents and clauses you used."
        explanation = str(raw.get("explanation") or "").strip()
        if not explanation:
            return None, "explanation must not be empty."
        letter = raw.get("letter_draft")
        letter = str(letter).strip() if letter else None
        bad = self.unknown_numbers(explanation + "\n" + (letter or ""))
        if bad:
            return None, (f"These numbers are not in any tool result of this run: {bad}. Copy numbers "
                          "exactly from the tool results or leave them out.")
        call_id = None
        if label in NEEDS_COMPUTE:
            named = raw.get("formula_id")
            call_id, problem = self.deciding_call(str(named) if named else None)
            if problem:
                return None, problem
        return {
            "classification": label,
            "compute_call_id": call_id,
            "evidence_refs": list(dict.fromkeys(refs))[:EVIDENCE_LIMIT],
            "explanation": explanation[:EXPLANATION_LIMIT],
            "letter_draft": letter[:LETTER_LIMIT] if letter and label == "CONFLICT" else None,
        }, ""


async def run_investigation(start: AgentRunStart, llm: LLM, backend: Backend, settings: Settings) -> str:
    """Runs one investigation end to end. Returns "COMPLETED", "FAILED" or "STOPPED"."""
    inputs = {"event": start.event.model_dump(), "finding": start.finding,
              "limits": start.limits.model_dump()}
    with tracing.span("investigation", "AGENT", inputs) as root:
        root.tag_trace({"run_id": start.run_id, "kind": "investigation",
                        "prompt_version": INVESTIGATION_PROMPT_VERSION, "llm_mode": llm.mode})
        status, outputs = await _run(start, llm, backend, settings)
        root.set_outputs(outputs)
        root.tag_trace({"status": status, "failure_reason": outputs.get("reason"),
                        "classification": outputs.get("classification"),
                        "tool_calls": outputs.get("tool_calls")})
        return status


async def _run(start: AgentRunStart, llm: LLM, backend: Backend, settings: Settings) -> tuple[str, dict[str, Any]]:
    run_id = start.run_id
    try:
        completion = await asyncio.wait_for(
            _loop(start, llm, backend, settings), timeout=max(1, start.limits.deadline_s)
        )
    except RunStopped as e:
        log.info("investigation %s stopped: %s", run_id, e)
        return "STOPPED", {"status": "STOPPED", "reason": str(e)}
    except (AgentFailed, TimeoutError, BackendUnavailable, Exception) as e:  # noqa: BLE001
        if isinstance(e, AgentFailed):
            reason, detail = e.reason, e.detail
        elif isinstance(e, TimeoutError):
            reason, detail = "MODEL_TIMEOUT", f"deadline of {start.limits.deadline_s}s passed"
        else:
            reason, detail = "MODEL_ERROR", f"{type(e).__name__}: {e}"
        log.warning("investigation %s FAILED (%s): %s -> backend runs the scripted one", run_id, reason, detail)
        try:
            await backend.fail(run_id, reason, detail)
        except Exception as e2:  # noqa: BLE001 - the backend's deadline falls back anyway
            log.error("investigation %s: could not report failure: %s", run_id, e2)
        return "FAILED", {"status": "FAILED", "reason": reason, "detail": detail}
    await backend.complete(run_id, completion)
    log.info("investigation %s COMPLETED: %s, %d tool calls", run_id, completion.classification,
             completion.tool_calls)
    return "COMPLETED", {"status": "COMPLETED", "classification": completion.classification,
                         "tool_calls": completion.tool_calls, "evidence_refs": completion.evidence_refs}


async def _loop(start: AgentRunStart, llm: LLM, backend: Backend, settings: Settings) -> InvestigationCompletion:
    run_id, max_calls = start.run_id, start.limits.max_tool_calls
    specs = await backend.list_tools(run_id)
    tools = [_openai_tool(s) for s in specs] + [SUBMIT_FINDING_TOOL]
    known = {s.name for s in specs}
    finding = start.finding or start.event.payload
    context = getattr(start, "investigation", None) or {}  # Cost Memory's item, hypotheses
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": INVESTIGATION_SYSTEM},
        {"role": "user", "content": INVESTIGATION_USER.format(
            finding=json.dumps(finding, ensure_ascii=False),
            context=json.dumps(context, ensure_ascii=False), event_label=start.event.label,
            company_name=start.company.name, as_of_date=start.company.as_of_date,
            currency=start.company.currency, max_tool_calls=max_calls)},
    ]
    facts, used, corrections = InvestigationFacts(finding, context), 0, 0
    model = await llm.model_name()

    for _ in range(max_calls + 2 * MAX_CORRECTIONS + 2):
        offered = tools if used < max_calls else [SUBMIT_FINDING_TOOL]
        with tracing.span("llm_call", "CHAT_MODEL", {
                "messages": tracing.tail(messages), "tools": [t["function"]["name"] for t in offered]}) as sp:
            try:
                reply = await asyncio.wait_for(llm.chat(messages, offered), timeout=settings.llm_timeout_s)
            except TimeoutError as e:
                raise AgentFailed("MODEL_TIMEOUT", f"one model call took over {settings.llm_timeout_s}s") from e
            except Exception as e:  # noqa: BLE001
                raise AgentFailed("MODEL_ERROR", f"{type(e).__name__}: {e}") from e
            sp.set_outputs({"content": reply.content,
                            "tool_calls": [{"name": tc.name, "arguments": tc.arguments} for tc in reply.tool_calls]})
            sp.set_attributes({"model": model, "tool_calls_used": used})

        spent = used >= max_calls
        if not reply.tool_calls:
            if spent:
                raise AgentFailed("TOOL_LIMIT_REACHED", "no submit_finding after the last tool call")
            corrections += 1
            if corrections > MAX_CORRECTIONS:
                raise AgentFailed("NO_FINAL_ANSWER", (reply.content or "")[:200])
            messages.append({"role": "assistant", "content": reply.content or ""})
            messages.append({"role": "user", "content": "Use the tools, then finish by calling submit_finding."})
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

            if tc.name == "submit_finding":
                answer, problem = facts.check(args)
                if answer is not None:
                    return InvestigationCompletion(**answer, model=model,
                                                   prompt_version=INVESTIGATION_PROMPT_VERSION,
                                                   tool_calls=used)
                if spent:
                    raise AgentFailed("TOOL_LIMIT_REACHED", problem)
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
                raise AgentFailed("TOOL_LIMIT_REACHED", f"{tc.name} called after the last tool call")

            with tracing.span(f"tool:{tc.name}", "TOOL", args) as sp:
                result = await backend.call_tool(tc.name, run_id, args, call_id=tc.id)
                sp.set_outputs(result.model_dump(exclude_none=True))
            used = result.tool_calls_used if result.tool_calls_used is not None else used + 1
            if result.error and result.error.code in STOP_CODES:
                raise RunStopped(result.error.code)
            if result.error and result.error.code == "TOOL_LIMIT_REACHED":
                used = max_calls
            facts.record(tc.name, tc.id, result, args)
            _tool_msg(messages, tc.id, result.model_dump(exclude_none=True))

    raise AgentFailed("TOOL_LIMIT_REACHED" if used >= max_calls else "NO_FINAL_ANSWER",
                      "the model did not submit a finding")
