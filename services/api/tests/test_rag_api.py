from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_agent_client, get_repository
from api.domain.models import Category
from api.rag import retrieve
from api.routers.rag import router


def test_retrieve_rent_question_and_amount():
    rent = SimpleNamespace(
        id=uuid4(),
        booking_date=date(2025, 4, 1),
        amount=-380000,
        category=Category.RENT,
        counterparty="Main Street Real Estate",
        type="RENT PAYMENT",
        reference="April rent",
    )
    utility = SimpleNamespace(
        id=uuid4(),
        booking_date=date(2025, 4, 10),
        amount=-65000,
        category=Category.UTILITIES,
        counterparty="Energy",
        type="DIRECT DEBIT",
        reference="Electricity",
    )
    state = SimpleNamespace(history=[rent, utility], outflows=[], inflows=[])

    matches = retrieve(state, "When is the rent of 3800 euro due?")

    assert [item["id"] for item in matches] == [str(rent.id)]
    assert matches[0]["amount_cents"] == "-380000"
    assert retrieve(state, "unknown company", limit=12) == []


def test_public_question_route_passes_only_retrieved_records():
    rent = SimpleNamespace(
        id=uuid4(),
        booking_date=date(2025, 4, 1),
        amount=-380000,
        category=Category.RENT,
        counterparty="Landlord",
        type="RENT PAYMENT",
        reference="April rent",
    )
    state = SimpleNamespace(history=[rent], outflows=[], inflows=[])

    class Repository:
        @contextmanager
        def read(self):
            yield state

    class Agent:
        def ask(self, payload):
            assert payload["question"] == "When is the rent paid?"
            assert [source["id"] for source in payload["sources"]] == [str(rent.id)]
            return {
                "answer": "The rent was paid on 2025-04-01.",
                "sources": [*payload["sources"], {"id": "not-retrieved", "kind": "outflow"}],
                "model": "test-model",
            }

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_repository] = Repository
    app.dependency_overrides[get_agent_client] = Agent
    with TestClient(app) as client:
        response = client.post("/api/v1/assistant/ask", json={"question": "When is the rent paid?"})
    assert response.status_code == 200
    assert response.json()["sources"] == [
        {
            "id": str(rent.id),
            "kind": "TRANSACTION",
            "date": "2025-04-01",
            "amount": "-380000",
            "currency": "EUR",
            "category": "RENT",
            "counterparty": "Landlord",
            "description": "RENT PAYMENT April rent",
        }
    ]
    assert response.json()["model"] == "test-model"


def test_ai_unavailable_lists_records_instead_of_503():
    from api.agent import AgentUnavailableError

    rent = SimpleNamespace(
        id=uuid4(),
        booking_date=date(2025, 4, 1),
        amount=-380000,
        category=Category.RENT,
        counterparty="Landlord",
        type="RENT PAYMENT",
        reference="April rent",
    )
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
        response = client.post("/api/v1/assistant/ask", json={"question": "When is the rent paid?"})
    body = response.json()
    assert response.status_code == 200 and body["answer_source"] == "TEMPLATE"
    assert body["answer"].startswith("Records matching your question (1):")
    assert "Landlord" in body["answer"] and "-3 800,00 €" in body["answer"]
    assert body["sources"][0]["id"] == str(rent.id)
    assert (body["sources"][0]["kind"], body["sources"][0]["amount"]) == ("TRANSACTION", "-380000")


def test_english_filler_words_do_not_match_other_payments():
    energy = SimpleNamespace(
        id=uuid4(),
        booking_date=date(2025, 4, 10),
        amount=-65000,
        category=Category.UTILITIES,
        counterparty="City Energy & Utilities AG",
        type="DIRECT DEBIT",
        reference="Electricity April",
    )
    payroll = SimpleNamespace(
        id=uuid4(),
        booking_date=date(2025, 4, 28),
        amount=-860000,
        category=Category.PAYROLL,
        counterparty="Payroll (4 people)",
        type="SEPA TRANSFER",
        reference="Salaries, did pay April",
    )
    state = SimpleNamespace(history=[energy, payroll], outflows=[], inflows=[])
    assert [m["id"] for m in retrieve(state, "How much did I pay City Energy?")] == [str(energy.id)]


def test_an_unusable_model_answer_lists_the_records():
    rent = SimpleNamespace(
        id=uuid4(),
        booking_date=date(2025, 4, 1),
        amount=-380000,
        category=Category.RENT,
        counterparty="Landlord",
        type="RENT PAYMENT",
        reference="April rent",
    )
    state = SimpleNamespace(history=[rent], outflows=[], inflows=[])

    class Repository:
        @contextmanager
        def read(self):
            yield state

    class Agent:
        def ask(self, payload):
            return {"answer": "", "sources": payload["sources"]}

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_repository] = Repository
    app.dependency_overrides[get_agent_client] = Agent
    with TestClient(app) as client:
        response = client.post("/api/v1/assistant/ask", json={"question": "When is the rent paid?"})
    body = response.json()
    assert response.status_code == 200
    assert (body["answer_source"], body["fallback_reason"]) == (
        "TEMPLATE",
        "AI service unavailable: ValidationError",
    )
    assert [s["amount"] for s in body["sources"]] == ["-380000"]
