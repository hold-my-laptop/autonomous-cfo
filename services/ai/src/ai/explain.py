"""POST /v1/explain: plain-language texts for the recommendations the backend made.

The model only rewords facts. Every number in its text must appear in the facts we sent,
some phrases are forbidden, and lengths are capped. If anything fails, template texts
built from the same facts are returned instead (source TEMPLATE)."""

import asyncio
import json
import logging
import re
from datetime import date
from typing import Any

from .config import Settings
from .llm import LLM
from .prompts import EXPLAIN_SYSTEM
from .schemas import ExplainItem, ExplainRequest, ExplainResult, ExplainText

log = logging.getLogger("ai.explain")

FORBIDDEN = re.compile(r"\b(transferred|sent|guaranteed|approved by the bank|überwiesen|garantiert)\b", re.I)
NUMBER = re.compile(r"\d[\d.,   ]*\d|\d")
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"],
}
EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"recommendation_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["recommendation_id", "text"]}},
    },
    "required": ["headline", "items"],
}


# ---------- formatting (the same strings go to the model and into the templates) ----------
def money(cents: Any, lang: str) -> str:
    try:
        c = int(str(cents))
    except (TypeError, ValueError):
        return str(cents)
    sign, c = ("-" if c < 0 else ""), abs(c)
    euros, rest = divmod(c, 100)
    thousands = "," if lang == "en" else "."
    whole = f"{euros:,}".replace(",", thousands)
    if rest:
        return f"{sign}{whole}{'.' if lang == 'en' else ','}{rest:02d} €"
    return f"{sign}{whole} €"


def day(iso: Any, lang: str) -> str:
    try:
        d = date.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    m = MONTHS[lang][d.month - 1]
    return f"{d.day} {m} {d.year}" if lang == "en" else f"{d.day}. {m} {d.year}"


def _impact(state: dict[str, Any], lang: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if state.get("lowest") is not None:
        low = state["lowest"]
        if isinstance(low, dict):  # ForecastSummary.lowest = {date, closing}
            out["lowest_balance"] = money(low.get("closing"), lang)
            out["lowest_date"] = day(low.get("date"), lang)
        else:  # ImpactState.lowest = Money
            out["lowest_balance"] = money(low, lang)
    if state.get("lowest_date"):
        out["lowest_date"] = day(state["lowest_date"], lang)
    fb = state.get("first_breach")
    if isinstance(fb, dict):
        out["first_day_below_floor"] = day(fb.get("date"), lang)
        if fb.get("shortfall") is not None:
            out["shortfall"] = money(fb["shortfall"], lang)
    elif fb:
        out["first_day_below_floor"] = day(fb, lang)
    for k in ("days_below_floor",):
        if state.get(k) is not None:
            out[k] = state[k]
    if state.get("end_balance") is not None:
        out["end_balance"] = money(state["end_balance"], lang)
    return out


def build_facts(req: ExplainRequest) -> dict[str, Any]:
    lang = req.language
    items = []
    for it in req.items:
        s = it.subject or {}
        subject: dict[str, Any] = {}
        if it.kind == "RESCHEDULE_PAYMENT":
            subject = {"payment": s.get("ref"), "to": s.get("counterparty"), "amount": money(s.get("amount"), lang),
                       "from_date": day(s.get("from_date"), lang), "new_date": day(s.get("to_date"), lang)}
        elif it.kind == "CREDIT_LINE":
            subject = {"credit_line_amount": money(s.get("amount"), lang),
                       "available_from": day(s.get("available_from"), lang),
                       "note": "illustrative, not a bank offer"}
        items.append({
            "recommendation_id": it.recommendation_id, "kind": it.kind, "lane": it.lane,
            "subject": {k: v for k, v in subject.items() if v not in (None, "None")},
            "rules_not_met": [c.get("text") for c in it.failed_checks if c.get("text")],
            "before": _impact(it.before, lang), "after": _impact(it.after, lang),
        })
    return {
        "trigger": req.trigger_label,
        "cash_floor": money((req.mandate or {}).get("cash_floor"), lang) if (req.mandate or {}).get("cash_floor") else None,
        "forecast_before": _impact(req.forecast_before, lang),
        "forecast_after": _impact(req.forecast_after, lang),
        "number_of_recommendations": len(items),
        "recommendations": items,
        "other_facts": req.findings,
    }


# ---------- validation ----------
def _digits(token: str) -> str:
    return re.sub(r"\D", "", token).lstrip("0") or "0"


def allowed_numbers(facts: dict[str, Any]) -> set[str]:
    return {_digits(t) for t in NUMBER.findall(json.dumps(facts, ensure_ascii=False))}


def problems(text: str, allowed: set[str], limit: int) -> list[str]:
    found = []
    if len(text) > limit:
        found.append(f"longer than {limit} characters")
    if FORBIDDEN.search(text):
        found.append("forbidden phrase")
    bad = [t for t in NUMBER.findall(text) if _digits(t) not in allowed]
    if bad:
        found.append(f"numbers not in the facts: {bad}")
    return found


# ---------- templates ----------
def template(req: ExplainRequest, facts: dict[str, Any], reason: str | None) -> ExplainResult:
    en = req.language == "en"
    before, after = facts["forecast_before"], facts["forecast_after"]
    if before.get("lowest_balance") and before.get("lowest_date"):
        headline = (f"Without action, your cash would fall to {before['lowest_balance']} on {before['lowest_date']}."
                    if en else f"Ohne Maßnahmen fällt Ihr Kontostand am {before['lowest_date']} auf {before['lowest_balance']}.")
        if after.get("lowest_balance"):
            headline += (f" With these steps the lowest point is {after['lowest_balance']}." if en
                         else f" Mit diesen Schritten liegt der Tiefstand bei {after['lowest_balance']}.")
    else:
        headline = "Here is what Vero found." if en else "Das hat Vero gefunden."
    texts = []
    for it, f in zip(req.items, facts["recommendations"]):
        texts.append(ExplainText(recommendation_id=it.recommendation_id, text=_item_text(it, f, en)[:300]))
    return ExplainResult(headline=headline[:200], items=texts, source="TEMPLATE", fallback_reason=reason)


def _item_text(it: ExplainItem, f: dict[str, Any], en: bool) -> str:
    s = f["subject"]
    if it.kind == "RESCHEDULE_PAYMENT":
        what = (f"Pay {s.get('to', 'this payment')} ({s.get('amount')}) on {s.get('new_date')} instead of {s.get('from_date')}."
                if en else f"{s.get('to', 'Zahlung')} ({s.get('amount')}) am {s.get('new_date')} statt am {s.get('from_date')} zahlen.")
    else:
        what = (f"A short-term credit line of {s.get('credit_line_amount')} from {s.get('available_from')} (illustrative, not a bank offer)."
                if en else f"Ein kurzfristiger Kreditrahmen von {s.get('credit_line_amount')} ab {s.get('available_from')} (Beispiel, kein Bankangebot).")
    rule = f["rules_not_met"][0] if f["rules_not_met"] else ""
    lane = {
        "DONE_WITHIN_MANDATE": "Done within your rules (simulated)." if en else "Im Rahmen Ihrer Regeln erledigt (simuliert).",
        "NEEDS_APPROVAL": ("Needs your approval." if en else "Braucht Ihre Freigabe.") + (f" {rule}" if rule else ""),
        "NOT_ALLOWED": ("Not allowed by your rules." if en else "Nach Ihren Regeln nicht erlaubt.") + (f" {rule}" if rule else ""),
    }.get(it.lane, "")
    return f"{what} {lane}".strip()


# ---------- entry point ----------
async def explain(req: ExplainRequest, llm: LLM, settings: Settings) -> ExplainResult:
    facts = build_facts(req)
    if llm.mode == "stub" or not req.items:
        return template(req, facts, "stub mode" if llm.mode == "stub" else None)
    lang = "English" if req.language == "en" else "German"
    messages = [
        {"role": "system", "content": EXPLAIN_SYSTEM.format(language=lang)},
        {"role": "user", "content": "Facts:\n" + json.dumps(facts, ensure_ascii=False, indent=1)},
    ]
    try:
        reply = await asyncio.wait_for(
            llm.chat(messages, json_schema=EXPLAIN_SCHEMA, max_tokens=500), timeout=settings.explain_timeout_s)
        data = json.loads(reply.content or "{}")
        result = ExplainResult(headline=data["headline"], source="MODEL",
                               items=[ExplainText(**i) for i in data["items"]])
    except Exception as e:  # noqa: BLE001
        log.warning("explain %s: model output unusable (%s), using templates", req.run_id, e)
        return template(req, facts, f"model error: {type(e).__name__}")

    allowed = allowed_numbers(facts)
    wanted = {i.recommendation_id for i in req.items}
    got = {i.recommendation_id: i for i in result.items}
    issues = [] if set(got) == wanted else ["recommendation ids do not match"]
    issues += problems(result.headline, allowed, 200)
    for i in result.items:
        issues += [f"{i.recommendation_id}: {p}" for p in problems(i.text, allowed, 300)]
    if issues:
        log.warning("explain %s: rejected model text: %s", req.run_id, issues)
        return template(req, facts, "; ".join(issues)[:300])
    result.items = [got[i.recommendation_id] for i in req.items]
    return result
