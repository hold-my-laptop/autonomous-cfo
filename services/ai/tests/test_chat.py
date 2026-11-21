"""Chat: read-only tools, number checks, one correction round, safe fallback."""

import asyncio

from fastapi.testclient import TestClient

from ai.chat import ChatRequest, ChatTurn, answer, facts, readable
from ai.llm import StubLLM
from ai.main import create_app

from conftest import FakeBackend, ScriptedLLM

OVERVIEW = {
    "company": {"name": "Pizzeria Amalfi", "balance": "40196", "as_of_date": "2025-05-05", "financial_version": 2},
    "cash_floor": "-500000",
    "forecast_summary": {"first_breach": {"date": "2025-06-15", "closing": "-751625", "shortfall": "251625"},
                         "lowest": {"date": "2025-07-03", "closing": "-2276082"}, "days_below_floor": 50},
    "headline": {"text": "Without action your cash falls.", "source": "TEMPLATE"},
    "lanes": {}, "pending_count": 2, "latest_run": {"run_id": "x", "status": "SUCCEEDED"},
}


class FakePublic:
    def __init__(self):
        self.paths = []

    async def get(self, path, params=None):
        self.paths.append(path)
        if path == "/overview":
            return OVERVIEW
        if path == "/recommendations":
            return [{"id": "r1", "kind": "CREDIT_LINE", "lane": "NEEDS_APPROVAL", "status": "PENDING",
                     "execution": "SIMULATED", "subject": {"kind": "CREDIT_LINE", "amount": "2500000",
                                                           "available_from": "2025-06-15"},
                     "impact": {"after_if_executed": {"first_breach": None, "lowest": "223918"}},
                     "explanation": {"text": "A credit line.", "source": "TEMPLATE"}, "checks": []}]
        return {}


def ask(llm, q="When is my cash lowest?", settings=None, api=None):
    from ai.config import Settings
    s = settings or Settings(llm_mode="live", llm_base_url="http://unused/v1")
    return asyncio.run(answer(ChatRequest(question=q), llm, api or FakePublic(), s))


def test_readable_formats_money_and_dates_and_drops_ids():
    out = readable(OVERVIEW, "en")
    assert out["company"]["balance"] == "401.96 €"
    assert out["forecast_summary"]["lowest"] == {"date": "3 July 2025", "closing": "-22,760.82 €"}
    assert "financial_version" not in out["company"]
    assert out["forecast_summary"]["days_below_floor"] == 50


def test_answer_with_numbers_from_tools_is_accepted():
    llm = ScriptedLLM([("get_overview", {}), "Your cash is lowest on 3 July 2025 at -22,760.82 €."])
    r = ask(llm)
    assert r.source == "MODEL" and r.tools_used == ["get_overview", "get_overview"]
    assert "-22,760.82 €" in r.answer


def test_invented_number_gets_one_correction():
    llm = ScriptedLLM([("get_overview", {}), "You will be short by about 23,000 €.",
                       "Your lowest point is -22,760.82 € on 3 July 2025."])
    r = ask(llm)
    assert r.source == "MODEL" and "23,000" not in r.answer
    correction = llm.seen_messages[-1][-1]["content"]
    assert "numbers not in any tool result" in correction


def test_invented_numbers_twice_fall_back():
    llm = ScriptedLLM([("get_overview", {}), "About 23,000 €.", "Roughly 24,000 €."])
    r = ask(llm)
    assert r.source == "FALLBACK" and "numbers not in any tool result" in r.fallback_reason
    assert "23,000" not in r.answer and "24,000" not in r.answer


def test_numbers_from_the_question_are_allowed_and_think_is_stripped():
    llm = ScriptedLLM([("list_recommendations", {}),
                       "<think>the user says 14 days, lowest becomes 2,239.18</think>"
                       "If you approve the credit line, the lowest point becomes 2,239.18 €. "
                       "I cannot simulate a 14 day delay of other payments."])
    r = ask(llm, q="What if I pay rent 14 days later?")
    assert r.source == "MODEL" and not r.answer.startswith("<think>") and "14 day" in r.answer


def test_unknown_tool_and_backend_error_do_not_crash():
    class Down(FakePublic):
        async def get(self, path, params=None):
            import httpx
            raise httpx.ConnectError("down")

    llm = ScriptedLLM([("nope", {}), ("get_overview", {}), "I cannot see your numbers right now."])
    r = ask(llm, api=Down())
    assert r.source == "MODEL" and r.tools_used == ["get_overview", "nope", "get_overview"]


def test_chat_endpoint_in_stub_mode_needs_no_token():
    from ai.config import Settings
    s = Settings(llm_mode="stub", internal_token="secret")
    app = create_app(s, llm=StubLLM(), backend=FakeBackend(), public=FakePublic())
    with TestClient(app) as c:
        r = c.post("/v1/chat", json={"question": "How am I doing?"})
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "FALLBACK" and "-22,760.82 €" in body["answer"]
        assert c.post("/v1/chat", json={"question": ""}).status_code == 422


NO_BREACH = {**OVERVIEW, "forecast_summary": {"first_breach": None, "lowest": {"date": "2025-06-01", "closing": "-39630"},
                                              "days_below_floor": 0, "end_balance": "239220"}, "pending_count": 0}


def test_facts_say_above_the_floor_when_the_lowest_point_is_negative_but_not_a_breach():
    f = facts(NO_BREACH, "en")
    assert f[0] == ("The balance never goes below the cash floor of -5,000 € in the forecast. "
                    "The lowest point is -396.30 € on 1 June 2025, which is above the floor.")
    assert "overdrawn" in f[1] and f[2] == "No recommendations wait for the owner's approval."
    assert facts(OVERVIEW, "en")[0].startswith("The balance goes below the cash floor of -5,000 € for the first time "
                                               "on 15 June 2025")
    assert "Mindestliquidität" in facts(NO_BREACH, "de")[0]


def test_every_turn_starts_with_the_overview_so_follow_ups_can_use_its_numbers():
    class NoBreach(FakePublic):
        async def get(self, path, params=None):
            self.paths.append(path)
            return NO_BREACH if path == "/overview" else {}

    from ai.config import Settings
    llm = ScriptedLLM(["Yes. Your lowest point is -396.30 € on 1 June 2025, which is above your -5,000 € floor."])
    req = ChatRequest(question="Really?", history=[
        ChatTurn(role="user", content="When does my cash get tight?"),
        ChatTurn(role="assistant", content="Your lowest point is -396.30 € on 1 June 2025.")])
    api = NoBreach()
    r = asyncio.run(answer(req, llm, api, Settings(llm_mode="live", llm_base_url="http://unused/v1")))
    assert r.source == "MODEL" and r.tools_used == ["get_overview"] and api.paths == ["/overview"]
    tool_msg = llm.seen_messages[0][-1]
    assert tool_msg["role"] == "tool" and "never goes below the cash floor" in tool_msg["content"]
