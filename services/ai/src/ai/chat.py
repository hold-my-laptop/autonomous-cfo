"""POST /v1/chat: the owner asks Vero questions ("Why do you suggest a credit line?",
"What happens if I approve the CHF move?", "When is my cash lowest?").

The model answers with read-only tools over the backend's public API. It never
computes: every number in the answer must appear in a tool result (amounts are given to
the model already formatted, e.g. "20,000 €") or in the question itself. An answer that
fails the check gets one correction round; if it still fails, a safe reply is returned
instead (source FALLBACK). Nothing here can change data: the tools are GET requests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, timedelta
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field

from . import tracing
from .config import Settings
from .explain import FORBIDDEN, NUMBER, _digits, day, money
from .llm import LLM

log = logging.getLogger("ai.chat")

MAX_ANSWER = 1200
CENTS = re.compile(r"^-?\d{1,15}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
THINK = re.compile(r"<think>.*?(</think>|$)", re.S)
DROP_KEYS = {"id", "run_id", "receipt_ids", "created_at", "decided_at", "activated_at", "version",
             "financial_version", "mandate_version", "engine_version", "source", "outflow_id", "forecast_id"}


# ---------- API ----------
class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    language: Literal["en", "de"] = "en"
    history: list[ChatTurn] = Field(default_factory=list, max_length=6)


class ChatResponse(BaseModel):
    answer: str
    source: Literal["MODEL", "FALLBACK"]
    tools_used: list[str]
    fallback_reason: str | None = None


# ---------- public backend API (read-only) ----------
class PublicApi(Protocol):
    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


class HttpPublicApi:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=settings.api_public_url.rstrip("/"),
                                       timeout=settings.backend_timeout_s, transport=transport)

    async def get(self, path, params=None):
        r = await self._http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._http.aclose()


# ---------- shaping data for the model ----------
def readable(value: Any, lang: str, key: str = "") -> Any:
    """Formats money (integer-cent strings) and dates, and drops ids the model does not need."""
    if isinstance(value, dict):
        return {k: readable(v, lang, k) for k, v in value.items() if k not in DROP_KEYS and v is not None}
    if isinstance(value, list):
        return [readable(v, lang, key) for v in value]
    if isinstance(value, str):
        if CENTS.match(value):
            return money(value, lang)
        if DATE.match(value):
            return day(value, lang)
    return value


async def _overview(api: PublicApi, args: dict[str, Any]) -> Any:
    o = await api.get("/overview")
    return {k: o.get(k) for k in ("company", "cash_floor", "forecast_summary", "headline", "lanes", "pending_count")}


async def _weekly_forecast(api: PublicApi, args: dict[str, Any]) -> Any:
    f = await api.get("/forecast", {"weekly": "true"})
    weeks = [{k: w[k] for k in ("week", "start_date", "end_date", "inflow", "outflow", "closing", "lowest",
                                "any_below_floor")} for w in f.get("weeks") or []]
    return {"as_of_date": f.get("as_of_date"), "start_balance": f.get("start_balance"),
            "cash_floor": f.get("cash_floor"), "summary": f.get("summary"), "weeks": weeks,
            "drivers": f.get("drivers"), "assumptions": f.get("assumptions"), "credit_lines": f.get("credit_lines")}


async def _days_around(api: PublicApi, args: dict[str, Any]) -> Any:
    try:
        center = date.fromisoformat(str(args.get("date")))
    except ValueError:
        return {"error": "date must be YYYY-MM-DD"}
    f = await api.get("/forecast")
    lo, hi = (center - timedelta(days=7)).isoformat(), (center + timedelta(days=7)).isoformat()
    days = [d for d in f.get("days") or [] if lo <= d["date"] <= hi]
    return {"days": days} if days else {"error": f"no forecast days near {center} (horizon starts {f.get('as_of_date')})"}


async def _recommendations(api: PublicApi, args: dict[str, Any]) -> Any:
    recs = await api.get("/recommendations")
    out = []
    for r in recs:
        out.append({"kind": r.get("kind"), "lane": r.get("lane"), "status": r.get("status"),
                    "execution": r.get("execution"), "subject": r.get("subject"), "impact": r.get("impact"),
                    "explanation": (r.get("explanation") or {}).get("text"),
                    "rules": [c.get("text") for c in r.get("checks") or [] if c.get("result") != "PASS"]})
    return {"recommendations": out}


async def _payments(api: PublicApi, args: dict[str, Any]) -> Any:
    rows = await api.get("/outflows")
    keep = ("ref", "counterparty", "category", "amount", "scheduled_date", "due_date", "latest_allowed_date",
            "protected", "original_amount", "original_currency")
    out = [{k: r.get(k) for k in keep} for r in rows]
    if args.get("movable_only"):
        out = [r for r in out if not r["protected"]]
    return {"payments": out}


async def _rules(api: PublicApi, args: dict[str, Any]) -> Any:
    return await api.get("/mandate")


async def _latest_run(api: PublicApi, args: dict[str, Any]) -> Any:
    runs = await api.get("/runs", {"limit": 1})
    if not runs:
        return {"error": "no runs yet"}
    r = runs[0]
    return {"mode": r.get("mode"), "status": r.get("status"), "trigger": (r.get("trigger") or {}).get("label"),
            "steps": [{"kind": s.get("kind"), "label": s.get("label")} for s in r.get("trace") or []]}


def _tool(name: str, description: str, props: dict[str, Any] | None = None, required: list[str] | None = None):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": props or {}, **({"required": required} if required else {})}}}


TOOLS = {
    "get_overview": (_overview, _tool(
        "get_overview", "Today's cash balance, the owner's cash floor, the forecast summary (first day below "
        "the floor, lowest point), what Vero analysed and how many recommendations wait for approval.")),
    "get_weekly_forecast": (_weekly_forecast, _tool(
        "get_weekly_forecast", "The 13-week cash forecast: closing and lowest balance per week, the drivers "
        "(why) and the assumptions behind it.")),
    "get_days_around": (_days_around, _tool(
        "get_days_around", "Daily forecast (closing balance, revenue, payments) for 7 days before and after a date.",
        {"date": {"type": "string", "description": "YYYY-MM-DD"}}, ["date"])),
    "list_recommendations": (_recommendations, _tool(
        "list_recommendations", "Vero's recommendations with their status, whether they need approval, the "
        "rules they touch and their impact on the forecast if executed (simulated).")),
    "list_payments": (_payments, _tool(
        "list_payments", "Known upcoming payments: who, how much, when, latest allowed date, protected or not.",
        {"movable_only": {"type": "boolean", "description": "only payments that may be postponed"}})),
    "get_rules": (_rules, _tool(
        "get_rules", "The owner's rules (mandate): cash floor, whether Vero may act alone, limits, protected "
        "payment categories, credit settings.")),
    "get_latest_run": (_latest_run, _tool(
        "get_latest_run", "What Vero did in its latest analysis, step by step, and whether the model (AGENT) "
        "or the fixed fallback pipeline (FALLBACK) produced it.")),
}

CHAT_SYSTEM = """You are Vero, a careful cash-flow assistant for a small business owner. Answer the owner's question in {language}.

Rules:
- Use the tools to look things up. Every number, amount and date in your answer must be copied exactly from a tool result (or the owner's question). Never calculate, add, subtract or estimate numbers yourself.
- Amounts in tool results are already formatted in euros; copy them as written.
- You only explain and look things up. You cannot execute, approve, transfer or change anything. All execution in this app is simulated, and credit lines are illustrative, not bank offers.
- If the owner asks "what if" about a recommendation, use its impact (after_if_executed). For changes that are not a recommendation, say you can only show the planner's options, and do not invent results.
- If the tools do not contain the answer, say so plainly.
- Answer in at most 5 short sentences, plain language, no tables, no markdown headings."""

FALLBACK_TEXT = {
    "en": "I could not put together an answer I can back with your numbers. Please try asking in a different way, or check the recommendations on the main screen.",
    "de": "Ich konnte keine Antwort geben, die ich mit Ihren Zahlen belegen kann. Bitte fragen Sie anders oder sehen Sie sich die Empfehlungen auf der Startseite an.",
}


def _clean(text: str | None) -> str:
    return THINK.sub("", text or "").strip()


def check(answer: str, allowed: set[str]) -> list[str]:
    found = []
    if not answer:
        found.append("empty answer")
    if len(answer) > MAX_ANSWER:
        found.append(f"longer than {MAX_ANSWER} characters")
    if FORBIDDEN.search(answer):
        found.append("forbidden phrase")
    bad = sorted({t.strip() for t in NUMBER.findall(answer) if _digits(t) not in allowed})
    if bad:
        found.append(f"numbers not in any tool result: {bad}")
    return found


def _allowed(texts: list[str]) -> set[str]:
    return {_digits(t) for text in texts for t in NUMBER.findall(text)}


async def answer(req: ChatRequest, llm: LLM, api: PublicApi, settings: Settings) -> ChatResponse:
    with tracing.span("chat", "AGENT", req.model_dump()) as root:
        root.tag_trace({"kind": "chat", "llm_mode": llm.mode})
        try:
            resp = await asyncio.wait_for(_answer(req, llm, api, settings), timeout=settings.chat_timeout_s)
        except TimeoutError:
            resp = ChatResponse(answer=FALLBACK_TEXT[req.language], source="FALLBACK", tools_used=[],
                                fallback_reason="timeout")
        except Exception as e:  # noqa: BLE001
            log.warning("chat failed: %s: %s", type(e).__name__, e)
            resp = ChatResponse(answer=FALLBACK_TEXT[req.language], source="FALLBACK", tools_used=[],
                                fallback_reason=f"{type(e).__name__}")
        root.set_outputs(resp.model_dump())
        root.tag_trace({"status": resp.source, "failure_reason": resp.fallback_reason})
        return resp


async def _stub_answer(req: ChatRequest, api: PublicApi) -> ChatResponse:
    """LLM_MODE=stub: a fixed template from the overview, so the UI can be built without a GPU."""
    o = readable(await _overview(api, {}), req.language)
    s = o.get("forecast_summary") or {}
    low = s.get("lowest") or {}
    text = (f"(Stub, not a model.) Your cash today is {(o.get('company') or {}).get('balance')}. "
            f"The lowest point in the forecast is {low.get('closing')} on {low.get('date')}. "
            f"{o.get('pending_count', 0)} recommendation(s) wait for your approval.")
    return ChatResponse(answer=text, source="FALLBACK", tools_used=["get_overview"], fallback_reason="stub mode")


async def _answer(req: ChatRequest, llm: LLM, api: PublicApi, settings: Settings) -> ChatResponse:
    if llm.mode == "stub":
        return await _stub_answer(req, api)
    lang = req.language
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": CHAT_SYSTEM.format(language="English" if lang == "en" else "German")},
        *[t.model_dump() for t in req.history],
        {"role": "user", "content": req.question},
    ]
    tools = [spec for _, spec in TOOLS.values()]
    evidence: list[str] = [req.question]
    used: list[str] = []
    corrections = 0

    for _ in range(settings.chat_max_tool_calls + 3):
        offered = tools if len(used) < settings.chat_max_tool_calls else None
        with tracing.span("llm_call", "CHAT_MODEL", {"messages": tracing.tail(messages)}) as sp:
            reply = await llm.chat(messages, offered)
            sp.set_outputs({"content": reply.content,
                            "tool_calls": [{"name": tc.name, "arguments": tc.arguments} for tc in reply.tool_calls]})

        if reply.tool_calls and offered:
            messages.append({"role": "assistant", "content": reply.content or "", "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in reply.tool_calls]})
            for tc in reply.tool_calls:
                result = await _run_tool(tc.name, tc.arguments, api, lang)
                used.append(tc.name)
                text = json.dumps(result, ensure_ascii=False)
                evidence.append(text)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
            continue

        text = _clean(reply.content)
        issues = check(text, _allowed(evidence))
        if not issues:
            return ChatResponse(answer=text, source="MODEL", tools_used=used)
        corrections += 1
        log.info("chat answer rejected (%d): %s", corrections, issues)
        if corrections > 1:
            return ChatResponse(answer=FALLBACK_TEXT[lang], source="FALLBACK", tools_used=used,
                                fallback_reason="; ".join(issues)[:300])
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": (
            f"Your answer cannot be shown: {'; '.join(issues)}. Rewrite it. Copy every number exactly from the "
            "tool results, and use the tools first if you have not looked the numbers up.")})

    return ChatResponse(answer=FALLBACK_TEXT[lang], source="FALLBACK", tools_used=used,
                        fallback_reason="too many steps")


async def _run_tool(name: str, raw_args: str, api: PublicApi, lang: str) -> Any:
    with tracing.span(f"tool:{name}", "TOOL", raw_args) as sp:
        if name not in TOOLS:
            result: Any = {"error": f"unknown tool {name}"}
        else:
            try:
                args = json.loads(raw_args or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
                result = readable(await TOOLS[name][0](api, args), lang)
            except (ValueError, json.JSONDecodeError) as e:
                result = {"error": f"bad arguments: {e}"}
            except httpx.HTTPError as e:
                result = {"error": f"backend unavailable: {type(e).__name__}"}
        sp.set_outputs(result)
        return result
