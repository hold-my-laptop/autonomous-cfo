import asyncio
import json

from ai.explain import build_facts, explain, money
from ai.llm import LLMReply
from ai.schemas import ExplainRequest

REQ = ExplainRequest(
    run_id="r1", trigger_label="You plan to close 1-30 June", mandate={"cash_floor": "-500000"},
    forecast_before={"lowest": {"date": "2025-07-03", "closing": "-2209760"}, "first_breach": None},
    forecast_after={"lowest": {"date": "2025-07-03", "closing": "-209760"}},
    items=[{"recommendation_id": "rec-1", "kind": "CREDIT_LINE", "lane": "NEEDS_APPROVAL",
            "subject": {"amount": "2000000", "available_from": "2025-06-28"},
            "failed_checks": [{"rule": "R5", "text": "A credit line always needs your approval."}],
            "before": {"lowest": "-2209760", "lowest_date": "2025-07-03"},
            "after": {"lowest": "-209760", "lowest_date": "2025-07-03"}}])


class JsonLLM:
    mode = "live"

    def __init__(self, data):
        self.data = data

    async def model_name(self):
        return "m"

    async def chat(self, messages, tools=None, *, json_schema=None, max_tokens=None):
        return LLMReply(content=json.dumps(self.data))


def go(llm, settings):
    return asyncio.run(explain(REQ, llm, settings))


def test_money_format():
    assert money("2000000", "en") == "20,000 €"
    assert money("-2209760", "en") == "-22,097.60 €"
    assert money("125050", "de") == "1.250,50 €"


def test_model_text_with_fact_numbers_is_accepted(settings):
    facts = build_facts(REQ)
    assert facts["recommendations"][0]["subject"]["credit_line_amount"] == "20,000 €"
    llm = JsonLLM({"headline": "Your cash dips to -22,097.60 € on 3 July 2025.",
                   "items": [{"recommendation_id": "rec-1",
                              "text": "A 20,000 € credit line from 28 June 2025 would cover it. It needs your approval."}]})
    r = go(llm, settings)
    assert r.source == "MODEL" and r.items[0].recommendation_id == "rec-1"


def test_invented_number_falls_back_to_template(settings):
    llm = JsonLLM({"headline": "You need 25,000 € fast.",
                   "items": [{"recommendation_id": "rec-1", "text": "Take 25,000 €."}]})
    r = go(llm, settings)
    assert r.source == "TEMPLATE" and "numbers not in the facts" in r.fallback_reason
    assert "20,000 €" in r.items[0].text and "Needs your approval" in r.items[0].text


def test_forbidden_phrase_falls_back(settings):
    llm = JsonLLM({"headline": "The money was transferred.",
                   "items": [{"recommendation_id": "rec-1", "text": "Done."}]})
    assert go(llm, settings).source == "TEMPLATE"


def test_broken_json_falls_back(settings):
    class Broken(JsonLLM):
        async def chat(self, *a, **k):
            return LLMReply(content="not json")
    assert go(Broken({}), settings).source == "TEMPLATE"
