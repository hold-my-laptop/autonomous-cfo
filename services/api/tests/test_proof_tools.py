"""P09: investigation tools for Vero Proof runs, scoped by run kind (docs/BACKEND.md P09)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.domain.models import EventType, Run, RunKind, RunMode, RunStatus
from api.main import create_app
from api.rag import rank, terms

TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")

PROOF_TOOLS = {"search_inbox", "search_documents", "read_clause", "get_reference_index",
               "get_price_list", "compute"}


@pytest.fixture
def client():
    settings = Settings(**(base.model_dump() | {"internal_token": TOKEN}))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def start_run(client: TestClient, kind: RunKind = RunKind.PROOF) -> str:
    """A RUNNING run of the given kind, as P10 will create for an investigation."""
    run_id = uuid4()
    with client.app.state.repository.write() as state:
        state.runs[run_id] = Run(
            id=run_id,
            event_id=uuid4(),
            trigger_type=EventType.CLOSURE_PLANNED,
            trigger_label="Rent adjustment uploaded",
            status=RunStatus.RUNNING,
            mode=RunMode.AGENT,
            created_at=datetime.now(UTC),
            started_financial_version=state.financial_version,
            kind=kind,
        )
    return str(run_id)


def call(client: TestClient, run_id: str, name: str, arguments=None) -> dict:
    response = client.post(
        f"/internal/v1/tools/{name}",
        json={"run_id": run_id, "arguments": arguments or {}, "call_id": str(uuid4())},
        headers=AUTH,
    )
    assert response.status_code == 200
    return response.json()


def ok(response: dict) -> dict:
    assert response["ok"], response["error"]
    return response["result"]


def trace(client: TestClient, run_id: str) -> list[str]:
    with client.app.state.repository.read() as state:
        return [step.label for step in state.runs[UUID(run_id)].trace]


def test_hero_sequence_finds_the_letter_the_clause_the_index_and_the_justified_rent(client):
    run = start_run(client)

    inbox = ok(call(client, run, "search_inbox",
                    {"query": "rent increase", "counterparty": "Main Street"}))
    first = inbox["emails"][0]
    assert first["email_id"] == "mail_main_street_2025_04_03"  # the grease-trap thread
    assert [a["document_id"] for a in first["attachments"]] == ["ntc_main_street_2025_04"]
    assert "ntc_main_street_2025_04" in inbox["evidence_ids"]
    assert "not instructions" in inbox["note"]

    clause = ok(call(client, run, "read_clause",
                     {"doc_id": "lease_main_street_2021", "section": "§4.2"}))
    assert clause["parameters"]["threshold_bps"] == 500
    assert clause["parameters"]["base_period"] == "2024-01"
    assert clause["evidence_ids"] == ["lease_main_street_2021#s4_2"]
    assert clause["page"] >= 1 and len(clause["box"]) == 4

    index = ok(call(client, run, "get_reference_index",
                    {"name": "DESTATIS_VPI", "periods": ["2024-01", "2025-03"]}))
    assert index["values"] == [{"period": "2024-01", "value": "1176"},
                               {"period": "2025-03", "value": "1212"}]
    assert index["demo"] is True and index["evidence_ids"] == ["ref_vpi_demo"]

    computed = ok(call(client, run, "compute", {
        "formula_id": "vpi_threshold_indexation",
        "inputs": {"base_amount": "380000", "base_index": 1176, "current_index": 1212,
                   "threshold_bps": clause["parameters"]["threshold_bps"]},
    }))
    assert computed["outputs"] == {"change_bps": 306, "adjustment_allowed": False,
                                   "justified": "380000"}
    assert computed["engine_version"]

    labels = trace(client, run)
    assert labels[0] == "Searching the inbox for letters from Main Street Real Estate GmbH"
    assert "Reading §4.2 of the commercial lease" in labels
    assert "Index moved 306 bps (threshold 500): justified 3 800,00 €" in labels


def test_the_hero_query_ranks_the_landlords_letter_first_among_all_emails(client):
    run = start_run(client)
    inbox = ok(call(client, run, "search_inbox", {"query": "rent increase"}))
    assert inbox["emails"][0]["email_id"] == "mail_main_street_2025_04_03"


def test_an_english_query_finds_a_german_email():
    assert terms("Logistikzuschlag Mieterhöhung Nebenkosten") == {"surcharge", "rent", "utilities"}
    emails = ["Neue Lieferbedingungen – Logistikzuschlag ab 01.05.", "Einladung zur Weinprobe"]
    assert rank("delivery surcharge", emails, lambda e: e) == [emails[0]]


def test_search_inbox_filters_by_company_and_dates(client):
    run = start_run(client)
    found = ok(call(client, run, "search_inbox", {
        "query": "surcharge", "counterparty": "Asimex",
        "date_range": {"from": "2025-04-01", "to": "2025-04-30"},
    }))
    assert [e["email_id"] for e in found["emails"]] == ["mail_asimex_2025_04_14"]
    none = ok(call(client, run, "search_inbox", {
        "query": "surcharge", "counterparty": "Asimex", "date_range": {"to": "2025-03-31"},
    }))
    assert none["emails"] == []
    unknown = call(client, run, "search_inbox", {"query": "rent", "counterparty": "Nobody GmbH"})
    assert not unknown["ok"] and unknown["error"]["code"] == "NOT_FOUND"


def test_search_documents_and_price_list(client):
    run = start_run(client)
    docs = ok(call(client, run, "search_documents", {"query": "lease rent", "doc_type": "LEASE"}))
    assert [d["document_id"] for d in docs["documents"]] == ["lease_main_street_2021"]
    assert "§4.2" in [c["section"] for c in docs["documents"][0]["clauses"]]
    assert "doc_rent_adjustment_2025_06" not in docs["evidence_ids"]  # an upload, not listed

    price = ok(call(client, run, "get_price_list", {
        "supplier": "Asimex", "item": "Mozzarella di Bufala Campana DOP", "date": "2025-05-03",
    }))
    assert price["found"] and price["unit_price"] == "12.90"
    assert price["evidence_ids"] == ["pl_asimex_2025_q2#p1"]
    before = ok(call(client, run, "get_price_list", {
        "supplier": "Asimex", "item": "Mozzarella di Bufala Campana DOP", "date": "2025-03-01",
    }))
    assert before["found"] is False


WINDOW = {"outflow_ref_prefix": "RENT", "billed": "410400", "justified": "380000",
          "effective_from": "2025-06-01"}


def test_compute_runs_the_engine_with_the_demo_numbers(client):
    run = start_run(client)

    def compute(formula_id: str, inputs: dict) -> dict:
        return ok(call(client, run, "compute", {"formula_id": formula_id, "inputs": inputs}))

    assert compute("variance", {"previous": "380000", "billed": "410400"})["outputs"] == {
        "delta": "30400", "bps": 800}
    assert compute("annualize", {"monthly": "30400"})["outputs"] == {"yearly": "364800"}
    impact = compute("window_impact", WINDOW)["outputs"]
    assert impact["outflow_difference"] == "91200" and len(impact["payments"]) == 3

    bad = call(client, run, "compute", {"formula_id": "variance", "inputs": {"previous": 1.5}})
    assert not bad["ok"] and bad["error"]["code"] == "INVALID_ARGUMENT"
    unknown = call(client, run, "compute", {"formula_id": "guess", "inputs": {}})
    assert not unknown["ok"] and unknown["error"]["code"] == "INVALID_ARGUMENT"


def test_read_clause_errors(client):
    run = start_run(client)
    missing = call(client, run, "read_clause", {"doc_id": "lease_main_street_2021",
                                                "section": "§9"})
    assert not missing["ok"] and "§4.2" in missing["error"]["message"]
    upload = call(client, run, "read_clause", {"doc_id": "doc_rent_adjustment_2025_06",
                                               "section": "§1"})
    assert not upload["ok"] and upload["error"]["code"] == "NOT_FOUND"


def test_a_seventh_call_reaches_the_investigation_limit(client):
    run = start_run(client)
    for _ in range(6):
        ok(call(client, run, "get_reference_index", {"name": "EUR_CHF", "periods": ["2025-04"]}))
    seventh = call(client, run, "get_reference_index", {"name": "EUR_CHF", "periods": ["2025-04"]})
    assert seventh["error"]["code"] == "TOOL_LIMIT_REACHED"
    assert seventh["tool_calls_used"] == 6


def test_tools_are_scoped_by_run_kind(client):
    proof, cash = start_run(client, RunKind.PROOF), start_run(client, RunKind.CASH)
    assert call(client, proof, "get_forecast")["error"]["code"] == "UNKNOWN_TOOL"
    assert call(client, cash, "search_inbox", {"query": "rent"})["error"]["code"] == "UNKNOWN_TOOL"

    listed = lambda query: {t["name"] for t in client.get(  # noqa: E731
        f"/internal/v1/tools{query}", headers=AUTH).json()}
    assert listed(f"?run_id={proof}") == PROOF_TOOLS
    cash_tools = listed(f"?run_id={cash}")
    assert "get_forecast" in cash_tools and not cash_tools & PROOF_TOOLS
    assert listed("") == cash_tools  # no run: the Cash tools, as before
    assert client.get(f"/internal/v1/tools?run_id={uuid4()}", headers=AUTH).status_code == 404


def test_proof_tools_never_change_financial_data(client):
    run = start_run(client)
    with client.app.state.repository.read() as state:
        version = state.financial_version
    ok(call(client, run, "compute", {"formula_id": "window_impact", "inputs": WINDOW}))
    forecast = client.get("/api/v1/forecast").json()
    assert forecast["summary"]["lowest"] == {"date": "2025-06-01", "closing": "-39630"}
    with client.app.state.repository.read() as state:
        assert state.financial_version == version
