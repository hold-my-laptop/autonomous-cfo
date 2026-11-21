import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.main import create_app

base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")
RENT_PDF = (base.proof_dataset_path.parent / "pdf" / "doc_rent_adjustment_2025_06.pdf").read_bytes()
QUIET = [
    (
        "Asimex Wholesale Ltd: new prices on AS-72217",
        "Every difference matches the Price list Q2 2025",
        None,
    ),
    (
        "Fattoria La Vino GmbH: new prices on FLV-94818",
        "Every difference matches the Price list 2025",
        None,
    ),
    (
        "Asimex Wholesale Ltd: Logistikzuschlag (24,50 €) on AS-11607",
        "Verified: 24,50 € allowed by the evidence",
        "FALLBACK",
    ),
]


def post(client: TestClient, path: str):
    return client.post(f"/api/v1{path}", headers={"Idempotency-Key": str(uuid4())})


def digest(client: TestClient) -> dict:
    response = client.get("/api/v1/digest")
    assert response.status_code == 200
    return response.json()


def proof_block(client: TestClient) -> dict:
    return client.get("/api/v1/overview").json()["proof"]


def upload_rent(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("Mietanpassung.pdf", RENT_PDF, "application/pdf")},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.json()["status"] == "SUCCEEDED", response.text
    return response.json()


def rows(data: dict) -> list[tuple]:
    return [(c["title"], c["reason"], c["investigation_mode"]) for c in data["changes"]]


def without_surcharge_email(tmp_path: Path) -> Settings:
    """PROOF §7.5: the surcharge announcement is missing, so AS-11607 ends escalated."""
    data = json.loads(base.proof_dataset_path.read_text())
    data["mailbox"] = [e for e in data["mailbox"] if e["id"] != "mail_asimex_2025_04_14"]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(data))
    return Settings(**(base.model_dump() | {"proof_dataset_path": path}))


@pytest.fixture
def client():
    with TestClient(create_app(base)) as test_client:
        post(test_client, "/demo/reset")
        yield test_client


def test_after_reset_three_changes_are_handled_quietly(client: TestClient) -> None:
    data = digest(client)

    assert data["as_of"] == "2025-05-05"
    assert data["counts"] == {
        "changes": 3,
        "handled_quietly": 3,
        "needs_owner": 0,
        "routine_checks": 3,
    }
    assert rows(data) == QUIET
    assert all(c["quiet"] and c["actions"] == [] for c in data["changes"])
    assert [c["classification"] for c in data["changes"]] == ["VERIFIED"] * 3


def test_routine_checks_are_the_latest_payment_of_each_contract(client: TestClient) -> None:
    checks = digest(client)["routine_checks"]

    assert [
        (c["cost_ref"], c["date"], c["paid"], c["expected"], c["currency"], c["matches"])
        for c in checks
    ] == [
        ("RENT", "2025-05-01", "380000", "380000", "EUR", True),
        ("UTIL", "2025-04-10", "65000", "65000", "EUR", True),
        ("XFER", "2025-04-15", "416667", "416667", "CHF", True),
    ]
    assert (checks[2]["eur_amount"], checks[2]["fx_rate"]) == ("433334", "1.040")
    assert [c["document_id"] for c in checks] == [
        "lease_main_street_2021",
        "ctr_city_energy_2025",
        "ctr_luigi_2024",
    ]


def test_the_hero_upload_needs_the_owner_with_its_staged_actions(client: TestClient) -> None:
    accepted = upload_rent(client)
    data = digest(client)
    hero = data["changes"][-1]
    finding = client.get(f"/api/v1/findings/{hero['finding_id']}").json()

    assert data["counts"] == {
        "changes": 4,
        "handled_quietly": 3,
        "needs_owner": 1,
        "routine_checks": 3,
    }
    assert rows(data)[:3] == QUIET
    assert (hero["classification"], hero["status"], hero["quiet"]) == (
        "CONFLICT",
        "PENDING_APPROVAL",
        False,
    )
    assert hero["reason"] == "Conflict: 4 104,00 € billed, 3 800,00 € allowed"
    assert hero["investigation_mode"] == "FALLBACK"
    assert [(a["kind"], a["lane"], a["text"]) for a in hero["actions"]] == [
        ("KEEP_PAYMENT", "NEEDS_APPROVAL", "Keep paying 3 800,00 € for RENT"),
        ("SEND_LETTER", "NEEDS_APPROVAL", "Send a letter to Main Street Real Estate GmbH"),
    ]
    assert [a["recommendation_id"] for a in hero["actions"]] == finding["recommendation_ids"]
    assert proof_block(client) == {
        "open_findings": {"VERIFIED": 3, "UNEXPLAINED": 0, "CONFLICT": 1},
        "needs_owner": 1,
        "latest_run": {"run_id": accepted["run_id"], "status": "SUCCEEDED"},
    }


def test_findings_leave_the_digest_once_acknowledged_or_decided(client: TestClient) -> None:
    upload_rent(client)
    changes = digest(client)["changes"]
    keep, letter = changes[-1]["actions"]

    post(client, f"/findings/{changes[0]['finding_id']}/acknowledge")
    post(client, f"/recommendations/{keep['recommendation_id']}/approve")
    partly = digest(client)
    post(client, f"/recommendations/{letter['recommendation_id']}/reject")
    decided = digest(client)

    assert partly["counts"] == {
        "changes": 3,
        "handled_quietly": 2,
        "needs_owner": 1,
        "routine_checks": 3,
    }
    assert [a["kind"] for a in partly["changes"][-1]["actions"]] == ["SEND_LETTER"]
    assert decided["counts"] == {
        "changes": 2,
        "handled_quietly": 2,
        "needs_owner": 0,
        "routine_checks": 3,
    }
    assert rows(decided) == QUIET[1:]
    assert proof_block(client)["open_findings"] == {
        "VERIFIED": 2,
        "UNEXPLAINED": 0,
        "CONFLICT": 0,
    }


def test_reset_brings_the_digest_back(client: TestClient) -> None:
    before = digest(client)
    upload_rent(client)
    post(client, f"/findings/{before['changes'][0]['finding_id']}/acknowledge")
    post(client, "/demo/reset")

    assert digest(client) == before
    assert proof_block(client)["latest_run"] is None


def test_an_escalated_surcharge_needs_the_owner(tmp_path: Path) -> None:
    with TestClient(create_app(without_surcharge_email(tmp_path))) as client:
        post(client, "/demo/reset")
        data = digest(client)
        overview = proof_block(client)

    surcharge = data["changes"][-1]
    assert data["counts"] == {
        "changes": 3,
        "handled_quietly": 2,
        "needs_owner": 1,
        "routine_checks": 3,
    }
    assert (surcharge["classification"], surcharge["escalated"], surcharge["quiet"]) == (
        "UNEXPLAINED",
        True,
        False,
    )
    assert surcharge["investigation_mode"] == "FALLBACK"
    assert surcharge["actions"] == []
    assert overview["open_findings"] == {"VERIFIED": 2, "UNEXPLAINED": 1, "CONFLICT": 0}
    assert overview["needs_owner"] == 1


def test_the_cash_overview_ignores_proof_runs(client: TestClient) -> None:
    before = client.get("/api/v1/overview").json()
    upload_rent(client)
    after = client.get("/api/v1/overview").json()

    assert after["latest_run"] is None
    assert after["headline"] == before["headline"]
    assert after["forecast_summary"] == before["forecast_summary"]
