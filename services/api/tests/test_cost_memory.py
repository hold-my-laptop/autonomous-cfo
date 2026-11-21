import copy
import hashlib
import json
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.domain.importer import StatementImport
from api.domain.proof.dataset import ProofDatasetError, parse_dataset, validate_dataset
from api.main import create_app

settings = get_settings()
pytestmark = pytest.mark.skipif(not settings.statement_path.exists(), reason="sample CSV not found")
RAW: dict[str, Any] = json.loads(settings.proof_dataset_path.read_text(encoding="utf-8"))


@pytest.fixture
def client():
    with TestClient(create_app(settings)) as test_client:
        test_client.post("/api/v1/demo/reset")
        yield test_client


def cost_memory(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/v1/cost-memory")
    assert response.status_code == 200
    return {item["cost_ref"]: item for item in response.json()}


def sources(item: dict) -> dict[str, dict]:
    return {source["kind"]: source for source in item["sources"]}


def test_rent_expects_the_lease_amount_backed_by_the_statement(client: TestClient) -> None:
    rent = cost_memory(client)["RENT"]
    assert (rent["kind"], rent["expected_amount"], rent["currency"]) == ("FIXED", "380000", "EUR")
    assert (rent["frequency"], rent["due_day"], rent["outflow_ref_prefix"]) == (
        "MONTHLY",
        1,
        "RENT",
    )
    by_kind = sources(rent)
    assert set(by_kind) == {"STATEMENT", "CONTRACT"}
    assert by_kind["CONTRACT"] | {"transaction_ids": []} == {
        "kind": "CONTRACT",
        "date": "2021-01-15",
        "document_id": "lease_main_street_2021",
        "clause_id": "s4_1",
        "transaction_ids": [],
        "amount": "380000",
        "currency": "EUR",
    }
    statement = by_kind["STATEMENT"]
    assert (statement["amount"], statement["date"]) == ("380000", "2025-05-01")
    rows = client.get("/api/v1/transactions", params={"category": "RENT"}).json()
    paid = {t["id"]: t["amount"] for t in rows}
    assert len(statement["transaction_ids"]) == 5
    assert {paid[i] for i in statement["transaction_ids"]} == {"-380000"}


def test_chf_tranche_is_expected_in_chf(client: TestClient) -> None:
    xfer = cost_memory(client)["XFER"]
    assert (xfer["expected_amount"], xfer["currency"]) == ("416667", "CHF")
    assert (xfer["eur_amount"], xfer["fx_rate"], xfer["outflow_ref_prefix"]) == (
        "433334",
        "1.040",
        "CHF",
    )
    by_kind = sources(xfer)
    assert (by_kind["CONTRACT"]["document_id"], by_kind["CONTRACT"]["clause_id"]) == (
        "ctr_luigi_2024",
        "s2",
    )
    assert (by_kind["STATEMENT"]["amount"], by_kind["STATEMENT"]["currency"]) == (
        "416667",
        "CHF",
    )
    assert len(by_kind["STATEMENT"]["transaction_ids"]) == 4


def test_fixed_costs_without_a_contract_come_from_the_statement(client: TestClient) -> None:
    memory = cost_memory(client)
    assert list(memory) == ["RENT", "UTIL", "XFER", "PAY", "SOC", "WTAX", "ASIMEX", "FATTORIA"]
    assert memory["UTIL"]["expected_amount"] == "65000"
    assert sources(memory["UTIL"])["CONTRACT"]["document_id"] == "ctr_city_energy_2025"
    expected = {"PAY": ("860000", 16), "SOC": ("265000", 4), "WTAX": ("88000", 4)}
    for ref, (amount, rows) in expected.items():
        item = memory[ref]
        assert item["expected_amount"] == amount
        assert [s["kind"] for s in item["sources"]] == ["STATEMENT"]
        assert len(item["sources"][0]["transaction_ids"]) == rows


def test_suppliers_carry_the_list_prices_valid_today(client: TestClient) -> None:
    memory = cost_memory(client)
    for ref, price_list_id in (("FATTORIA", "pl_fattoria_2025"), ("ASIMEX", "pl_asimex_2025_q2")):
        item = memory[ref]
        assert (item["kind"], item["expected_amount"], item["category"]) == (
            "SUPPLIER",
            None,
            "VARIABLE_COST",
        )
        listed = next(d for d in RAW["documents"] if d["id"] == price_list_id)["price_list"]
        assert [(p["description"], p["unit_price"], p["vat_rate"]) for p in item["prices"]] == [
            (i["description"], i["unit_price"], i["vat_rate"]) for i in listed["items"]
        ]
        assert {p["document_id"] for p in item["prices"]} == {price_list_id}
    fattoria = {p["description"]: p["unit_price"] for p in memory["FATTORIA"]["prices"]}
    assert fattoria["Chianti Classico DOCG 2021, 0,75 l"] == "9.40"
    assert fattoria["Olio Extra Vergine di Oliva, 5 l"] == "31.50"
    asimex = {p["description"]: p["unit_price"] for p in memory["ASIMEX"]["prices"]}
    assert asimex["Mozzarella di Bufala Campana DOP"] == "12.90"
    assert "Logistikzuschlag" not in asimex
    invoices = [s for s in memory["ASIMEX"]["sources"] if s["kind"] == "INVOICE"]
    assert [(s["document_id"], s["amount"]) for s in invoices] == [
        ("inv_AS-80497", "58474"),
        ("inv_AS-72217", "68723"),
        ("inv_AS-11607", "62403"),
    ]
    assert "terms_asimex_2025" in {s["document_id"] for s in memory["ASIMEX"]["sources"]}


def test_cost_memory_is_rebuilt_identically_on_reset(client: TestClient) -> None:
    before = client.get("/api/v1/cost-memory").json()
    client.post("/api/v1/demo/reset")
    assert client.get("/api/v1/cost-memory").json() == before


def test_documents_list_only_the_owners_documents(client: TestClient) -> None:
    listed = client.get("/api/v1/documents").json()
    expected = {d["id"] for d in RAW["documents"] if d["source"] == "DOCUMENTS"}
    assert {d["id"] for d in listed} == expected
    assert "ntc_main_street_2025_04" not in expected
    assert "doc_rent_adjustment_2025_06" not in expected
    assert [d["date"] for d in listed] == sorted(d["date"] for d in listed)
    lease = next(d for d in listed if d["id"] == "lease_main_street_2021")
    assert lease["counterparty"] == {"id": "cp_main_street", "name": "Main Street Real Estate GmbH"}
    assert (lease["page_count"], lease["source"], lease["cost_ref"]) == (2, "DOCUMENTS", "RENT")
    assert lease["file_url"] == "/api/v1/documents/lease_main_street_2021/file"


@pytest.mark.parametrize(
    "document_id", ["ntc_main_street_2025_04", "doc_rent_adjustment_2025_06", "unknown"]
)
def test_hidden_or_unknown_documents_are_not_found(client: TestClient, document_id: str) -> None:
    for path in (f"/api/v1/documents/{document_id}", f"/api/v1/documents/{document_id}/file"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"


def test_document_detail_has_pages_values_clauses_and_boxes(client: TestClient) -> None:
    invoice = client.get("/api/v1/documents/inv_FLV-16261").json()
    assert invoice["lines"][0] == {
        "id": "l1",
        "description": "Chianti Classico DOCG 2021, 0,75 l",
        "quantity": "30",
        "unit": "Fl.",
        "unit_price": "8.90",
        "vat_rate": 1900,
        "net": "26700",
        "gross": None,
    }
    assert invoice["totals"] == {"net": "87463", "vat": "16258", "gross": "103721"}
    highlights = {h["ref"]: h for h in invoice["highlights"]}
    assert highlights["totals.gross"]["text"] == "1.037,21 €"
    assert len(highlights["totals.gross"]["box"]) == 4
    lease = client.get("/api/v1/documents/lease_main_street_2021").json()
    clause = next(c for c in lease["clauses"] if c["id"] == "s4_2")
    assert clause["parameters"]["threshold_bps"] == 500
    assert clause["page"] == 1 and len(clause["box"]) == 4
    assert [p["page"] for p in lease["pages"]] == [1, 2]
    assert all(len(b["box"]) == 4 for p in lease["pages"] for b in p["blocks"])


def test_document_file_is_the_stored_pdf(client: TestClient) -> None:
    response = client.get("/api/v1/documents/inv_AS-11607/file")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    stored = client.get("/api/v1/documents/inv_AS-11607").json()["sha256"]
    assert hashlib.sha256(response.content).hexdigest() == stored


def test_decimals_are_documented_as_strings() -> None:
    schemas = create_app(settings).openapi()["components"]["schemas"]
    unit_price = schemas["ExpectedPrice"]["properties"]["unit_price"]
    assert unit_price["type"] == "string"
    assert unit_price["pattern"] == r"^-?[0-9]{1,9}(\.[0-9]{1,6})?$"
    assert schemas["DocumentLine"]["properties"]["quantity"]["type"] == "string"


def test_a_mailbox_document_must_be_attached_to_an_email(
    sample_statement: StatementImport,
) -> None:
    data = copy.deepcopy(RAW)
    for email in data["mailbox"]:
        email["attachments"] = [a for a in email["attachments"] if a != "ntc_main_street_2025_04"]
    with pytest.raises(ProofDatasetError, match="must be attached to an email"):
        validate_dataset(parse_dataset(data), sample_statement, settings.demo_as_of_date)


def test_list_prices_are_decimals_in_the_domain(client: TestClient) -> None:
    with client.app.state.repository.read() as state:
        fattoria = next(c for c in state.cost_memory if c.cost_ref == "FATTORIA")
    assert all(isinstance(p.unit_price, Decimal) for p in fattoria.prices)
