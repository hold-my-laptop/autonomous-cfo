from datetime import date
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_agent_client, get_repository
from api.domain.models import Category
from api.rag import retrieve
from api.routers.rag import router


def test_retrieve_turkish_rent_question_and_amount():
    rent = SimpleNamespace(id=uuid4(), booking_date=date(2025, 4, 1), amount=-380000,
                           category=Category.RENT, counterparty="Main Street Real Estate",
                           type="RENT PAYMENT", reference="April rent")
    utility = SimpleNamespace(id=uuid4(), booking_date=date(2025, 4, 10), amount=-65000,
                              category=Category.UTILITIES, counterparty="Energy",
                              type="DIRECT DEBIT", reference="Electricity")
    state = SimpleNamespace(history=[rent, utility], outflows=[], inflows=[])

    matches = retrieve(state, "Kira ödemesi 3800 euro ne zaman?")

    assert [item["id"] for item in matches] == [str(rent.id)]
    assert matches[0]["amount_cents"] == "-380000"
    assert retrieve(state, "bilinmeyen şirket", limit=12) == []


def test_public_question_route_passes_only_retrieved_records():
    rent = SimpleNamespace(id=uuid4(), booking_date=date(2025, 4, 1), amount=-380000,
                           category=Category.RENT, counterparty="Landlord",
                           type="RENT PAYMENT", reference="April rent")
    state = SimpleNamespace(history=[rent], outflows=[], inflows=[])

    class Repository:
        @contextmanager
        def read(self):
            yield state

    class Agent:
        def ask(self, payload):
            assert payload["question"] == "Kira ne zaman?"
            assert [source["id"] for source in payload["sources"]] == [str(rent.id)]
            return {"answer": "Kira 2025-04-01 tarihinde.",
                    "sources": payload["sources"], "model": "test-model"}

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_repository] = Repository
    app.dependency_overrides[get_agent_client] = Agent
    with TestClient(app) as client:
        response = client.post("/api/v1/assistant/ask", json={"question": "Kira ne zaman?"})
    assert response.status_code == 200
    assert response.json()["sources"][0]["id"] == str(rent.id)


def test_ai_unavailable_lists_records_instead_of_503():
    from api.agent import AgentUnavailableError

    rent = SimpleNamespace(id=uuid4(), booking_date=date(2025, 4, 1), amount=-380000,
                           category=Category.RENT, counterparty="Landlord",
                           type="RENT PAYMENT", reference="April rent")
    state = SimpleNamespace(history=[rent], outflows=[], inflows=[])

    class Repository:
        @contextmanager
        def read(self):
            yield state

    class Agent:
        def ask(self, payload):
            raise AgentUnavailableError("down")

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_repository] = Repository
    app.dependency_overrides[get_agent_client] = Agent
    with TestClient(app) as client:
        response = client.post("/api/v1/assistant/ask", json={"question": "Kira ne zaman?"})
    body = response.json()
    assert response.status_code == 200 and body["answer_source"] == "TEMPLATE"
    assert "Landlord" in body["answer"] and "-3 800,00 €" in body["answer"]
    assert body["sources"][0]["id"] == str(rent.id)


def test_english_filler_words_do_not_match_other_payments():
    energy = SimpleNamespace(id=uuid4(), booking_date=date(2025, 4, 10), amount=-65000,
                             category=Category.UTILITIES, counterparty="City Energy & Utilities AG",
                             type="DIRECT DEBIT", reference="Electricity April")
    payroll = SimpleNamespace(id=uuid4(), booking_date=date(2025, 4, 28), amount=-860000,
                              category=Category.PAYROLL, counterparty="Payroll (4 people)",
                              type="SEPA TRANSFER", reference="Salaries, did pay April")
    state = SimpleNamespace(history=[energy, payroll], outflows=[], inflows=[])
    assert [m["id"] for m in retrieve(state, "How much did I pay City Energy?")] == [str(energy.id)]
