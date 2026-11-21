import asyncio
import json

from fastapi.testclient import TestClient

from ai.config import Settings
from ai.llm import LLMReply, StubLLM
from ai.main import create_app
from ai.rag import AskRequest, Source, allowed_numbers, answer

from conftest import FakeBackend

SOURCE = {"id": "record-1", "kind": "transaction", "date": "2025-04-01",
          "amount_cents": "-380000", "currency": "EUR", "category": "RENT",
          "counterparty": "Landlord", "description": "April rent"}
OTHER = dict(SOURCE, id="record-2", date="2025-03-01", description="March rent")


class Model:
    """Plays back answers; each item is a dict (sent as JSON), a raw string or an exception."""

    mode = "live"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    async def model_name(self):
        return "test-model"

    async def chat(self, messages, tools=None, *, json_schema=None, max_tokens=None):
        assert json_schema is not None
        self.seen.append(list(messages))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return LLMReply(content=r if isinstance(r, str) else json.dumps(r))


def ask(model, question="When was the rent?", sources=(SOURCE,)):
    s = Settings(llm_mode="live", llm_base_url="http://unused/v1")
    return asyncio.run(answer(AskRequest(question=question, sources=list(sources)), model, s))


def test_grounded_answer_requires_existing_source():
    r = ask(Model({"answer": "Rent was paid on 1 April 2025.", "source_ids": ["record-1"]}))
    assert r.answer_source == "MODEL" and r.sources[0].id == "record-1" and r.model == "test-model"


def test_model_sees_formatted_amounts_and_dates():
    m = Model({"answer": "ok", "source_ids": ["record-1"]})
    ask(m, question="When was the rent paid?")
    records = json.loads(m.seen[0][1]["content"])["records"]
    assert records[0]["amount"] == "-3,800 €" and records[0]["date"] == "1 April 2025"
    assert "in English" in m.seen[0][0]["content"]
    assert "planned, so say they are due or expected" in m.seen[0][0]["content"]


def test_natural_spellings_of_cited_numbers_pass():
    """These were rejected before (only the raw '2025-04-01' / '3800.00' passed)."""
    for text, q in [("Rent of 3.800,00 € was paid on 1 April 2025.", "When was the rent paid?"),
                    ("Rent was paid on 1 April 2025: 3,800.00 EUR.", "When was the rent?"),
                    ("Rent of 3,800 € was paid on 01.04.2025.", "When was the rent?")]:
        r = ask(Model({"answer": text, "source_ids": ["record-1"]}), question=q)
        assert r.answer_source == "MODEL", text


def test_invented_amount_gets_one_correction():
    m = Model({"answer": "Rent was 9,999 EUR.", "source_ids": ["record-1"]},
              {"answer": "Rent was 3,800.00 EUR on 1 April 2025.", "source_ids": ["record-1"]})
    r = ask(m)
    assert r.answer_source == "MODEL" and "9,999" not in r.answer
    assert "not in the records you cited" in m.seen[1][-1]["content"]


def test_invented_amount_twice_lists_records_instead():
    r = ask(Model({"answer": "Rent was 9,999 EUR.", "source_ids": ["record-1"]},
                  {"answer": "About 10,000 EUR.", "source_ids": ["record-1"]}))
    assert r.answer_source == "TEMPLATE" and "not in the records" in r.fallback_reason
    assert "9,999" not in r.answer and "-3,800 €" in r.answer and "1 April 2025" in r.answer


def test_unknown_citation_is_rejected():
    r = ask(Model({"answer": "Rent was paid.", "source_ids": ["made-up"]},
                  {"answer": "Rent was paid.", "source_ids": []}))
    assert r.answer_source == "TEMPLATE"


def test_total_of_two_records_is_not_allowed():
    """A model adding up amounts itself must not pass (7,600 is in no record)."""
    bad = {"answer": "Total rent: 7,600.00 EUR.", "source_ids": ["record-1", "record-2"]}
    r = ask(Model(bad, bad), sources=(SOURCE, OTHER))
    assert r.answer_source == "TEMPLATE"


def test_model_error_and_bad_json_fall_back():
    assert ask(Model(RuntimeError("down"))).answer_source == "TEMPLATE"
    assert ask(Model("not json", "still not json")).answer_source == "TEMPLATE"


def test_numbers_from_question_are_allowed():
    allowed = allowed_numbers([Source(**SOURCE)], "Did I pay rent in the last 3 months?")
    assert {"3", "380000", "3800", "1", "4", "2025"} <= allowed


def test_no_sources_and_stub_mode():
    r = ask(Model(), sources=())
    assert r.answer_source == "TEMPLATE" and r.sources == []
    r = asyncio.run(answer(AskRequest(question="When is the rent paid?", sources=[SOURCE]), StubLLM(),
                           Settings(llm_mode="stub")))
    assert r.answer_source == "TEMPLATE" and r.answer.startswith("Records matching your question (1):")


def test_route_needs_token():
    app = create_app(Settings(llm_mode="stub", internal_token="secret"), llm=StubLLM(), backend=FakeBackend())
    with TestClient(app) as c:
        body = {"question": "When was the rent?", "sources": [SOURCE]}
        assert c.post("/v1/rag/answer", json=body).status_code == 401
        r = c.post("/v1/rag/answer", json=body, headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200 and r.json()["answer_source"] == "TEMPLATE"


def test_template_keeps_backend_relevance_order():
    r = ask(Model(RuntimeError("down")), sources=(OTHER, SOURCE))  # backend ranked OTHER first
    assert [s.id for s in r.sources] == ["record-2", "record-1"]
