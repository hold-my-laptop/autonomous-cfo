import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.domain.models import Category, RecommendationKind
from api.domain.policy import Lane, evaluate_proof_action
from api.main import create_app
from api.seed import initial_mandate
from api.state import demo_load_time

base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")
RENT_PDF = (base.proof_dataset_path.parent / "pdf" / "doc_rent_adjustment_2025_06.pdf").read_bytes()
LETTER = "Ihre Mietanpassung ab 01.06.2025 – Bitte um Erläuterung gemäß §4.2 Mietvertrag"
S0 = {
    "first_breach": None,
    "lowest": {"date": "2025-06-01", "closing": "-39630"},
    "days_below_floor": 0,
    "end_balance": "239220",
}


def post(client: TestClient, path: str, body: dict | None = None):
    return client.post(f"/api/v1{path}", json=body, headers={"Idempotency-Key": str(uuid4())})


def client_for(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def vpi_above_threshold(tmp_path: Path) -> Settings:
    """PROOF §7.5: the March 2025 VPI at 1236 instead of 1212 (510 bps, above 500)."""
    data = json.loads(base.proof_dataset_path.read_text())
    for series in data["reference_values"]:
        if series["name"] == "DESTATIS_VPI":
            for value in series["values"]:
                if value["period"] == "2025-03":
                    value["value"] = "1236"
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(data))
    return Settings(**(base.model_dump() | {"proof_dataset_path": path}))


def upload_rent(client: TestClient) -> dict:
    post(client, "/demo/reset")
    response = client.post(
        "/api/v1/documents",
        files={"file": ("Mietanpassung.pdf", RENT_PDF, "application/pdf")},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.json()["status"] == "SUCCEEDED", response.text
    run = client.get(f"/api/v1/runs/{response.json()['run_id']}").json()
    [finding_id] = run["finding_ids"]
    return client.get(f"/api/v1/findings/{finding_id}").json()


def actions(client: TestClient, finding: dict) -> list[dict]:
    listed = client.get("/api/v1/recommendations").json()
    return [r for r in listed if r["finding_id"] == finding["id"]]


def summary(client: TestClient) -> dict:
    data = client.get("/api/v1/forecast").json()["summary"]
    return {k: data[k] for k in ("first_breach", "lowest", "days_below_floor", "end_balance")}


def test_hero_actions_wait_for_the_owner() -> None:
    with client_for(base) as client:
        finding = upload_rent(client)
        keep, letter = actions(client, finding)

    assert (finding["classification"], finding["status"]) == ("CONFLICT", "PENDING_APPROVAL")
    assert finding["recommendation_ids"] == [keep["id"], letter["id"]]
    assert [(r["kind"], r["lane"], r["status"]) for r in (keep, letter)] == [
        ("KEEP_PAYMENT", "NEEDS_APPROVAL", "PENDING"),
        ("SEND_LETTER", "NEEDS_APPROVAL", "PENDING"),
    ]
    assert keep["subject"] | {"finding_id": None, "payments": None} == {
        "kind": "KEEP_PAYMENT",
        "finding_id": None,
        "cost_ref": "RENT",
        "counterparty": "Main Street Real Estate GmbH",
        "category": "RENT",
        "amount": "380000",
        "billed_amount": "410400",
        "effective_from": "2025-06-01",
        "payments": None,
    }
    assert [p["ref"] for p in keep["subject"]["payments"]] == ["RENT-06", "RENT-07", "RENT-08"]
    assert keep["reason"]["disputed"] == "30400"
    assert keep["explanation"]["text"].endswith(
        "Never withhold rent: pay the contractual amount and ask in writing."
    )
    assert letter["subject"]["letter_subject"] == LETTER
    assert letter["subject"]["language"] == "de"
    assert [c["rule"] for c in letter["checks"]] == ["R7", "PAUSE"]
    assert [c["rule"] for c in keep["checks"]] == ["R4", "R6", "PAUSE"]


def test_approving_the_hero_actions_resolves_the_finding() -> None:
    with client_for(base) as client:
        finding = upload_rent(client)
        before = summary(client)
        version = client.get("/api/v1/company").json()["financial_version"]
        decided = [
            post(client, f"/recommendations/{r['id']}/approve").json()
            for r in actions(client, finding)
        ]
        resolved = client.get(f"/api/v1/findings/{finding['id']}").json()
        receipts = [
            client.get(f"/api/v1/receipts/{i}").json()
            for d in decided
            for i in d["recommendation"]["receipt_ids"]
        ]
        log = client.get("/api/v1/audit").json()
        after = summary(client)
        after_version = client.get("/api/v1/company").json()["financial_version"]

    assert [d["recommendation"]["status"] for d in decided] == ["APPROVED_EXECUTED"] * 2
    assert all(
        d["recommendation"]["executed"]["confirmation_ref"].startswith("SIM-") for d in decided
    )
    assert [r["kind"] for r in receipts] == ["PROPOSAL", "DECISION", "PROPOSAL", "DECISION"]
    assert receipts[1]["decision"] == "APPROVED"
    assert (resolved["status"], resolved["classification"]) == ("RESOLVED", "CONFLICT")
    assert resolved["history"][-1]["label"] == (
        "You decided: keep paying 3 800,00 € for RENT (approved); send a letter to "
        "Main Street Real Estate GmbH (approved)"
    )
    assert not resolved["in_digest"]
    assert before == after == S0
    assert after_version == version
    approvals = [(e["actor"], e["agent"], e["action"]) for e in log[-2:]]
    assert approvals == [
        ("OWNER", "PROOF", "Approved: keep paying 3 800,00 € for RENT (simulated)"),
        ("OWNER", "PROOF", "Approved: send a letter to Main Street Real Estate GmbH (simulated)"),
    ]


def test_a_rejected_letter_also_settles_the_finding() -> None:
    with client_for(base) as client:
        finding = upload_rent(client)
        keep, letter = actions(client, finding)
        post(client, f"/recommendations/{letter['id']}/reject")
        waiting = client.get(f"/api/v1/findings/{finding['id']}").json()
        post(client, f"/recommendations/{keep['id']}/approve")
        resolved = client.get(f"/api/v1/findings/{finding['id']}").json()

    assert waiting["status"] == "PENDING_APPROVAL"
    assert resolved["status"] == "RESOLVED"
    assert resolved["history"][-1]["label"].endswith(
        "send a letter to Main Street Real Estate GmbH (rejected)"
    )


def test_vpi_variant_changes_the_rent_and_the_forecast_but_not_the_backtest(
    tmp_path: Path,
) -> None:
    with client_for(vpi_above_threshold(tmp_path)) as client:
        finding = upload_rent(client)
        update, letter = actions(client, finding)
        backtest = client.get("/api/v1/forecast/backtest").json()
        before = summary(client)
        version = client.get("/api/v1/company").json()["financial_version"]

        approved = post(client, f"/recommendations/{update['id']}/approve").json()

        rents = [o for o in client.get("/api/v1/outflows").json() if o["ref"].startswith("RENT-")]
        after = summary(client)
        after_backtest = client.get("/api/v1/forecast/backtest").json()
        after_version = client.get("/api/v1/company").json()["financial_version"]
        still_waiting = client.get(f"/api/v1/findings/{finding['id']}").json()

    assert finding["propagation"]["decision"] == "UPDATE"
    assert [(r["kind"], r["lane"]) for r in (update, letter)] == [
        ("UPDATE_RECURRING_AMOUNT", "NEEDS_APPROVAL"),
        ("SEND_LETTER", "NEEDS_APPROVAL"),
    ]
    assert (update["subject"]["from_amount"], update["subject"]["to_amount"]) == (
        "380000",
        "399388",
    )
    assert update["reason"]["disputed"] == "11012"
    assert approved["recommendation"]["status"] == "APPROVED_EXECUTED"
    assert [(o["ref"], o["amount"]) for o in rents] == [
        ("RENT-06", "399388"),
        ("RENT-07", "399388"),
        ("RENT-08", "399388"),
    ]
    assert after_version == version + 1
    assert after != before
    assert int(after["lowest"]["closing"]) == int(before["lowest"]["closing"]) - 19388
    assert after_backtest["forecast_id"] != backtest["forecast_id"]
    assert {k: v for k, v in after_backtest.items() if k != "forecast_id"} == {
        k: v for k, v in backtest.items() if k != "forecast_id"
    }
    assert still_waiting["status"] == "PENDING_APPROVAL"


def test_an_amount_change_makes_a_pending_cash_proposal_stale(tmp_path: Path) -> None:
    with client_for(vpi_above_threshold(tmp_path)) as client:
        finding = upload_rent(client)
        version = client.get("/api/v1/company").json()["financial_version"]
        post(
            client,
            "/events",
            {
                "type": "CLOSURE_PLANNED",
                "expected_financial_version": version,
                "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"},
            },
        )
        cash = [
            r
            for r in client.get("/api/v1/recommendations").json()
            if r["status"] == "PENDING" and r["finding_id"] is None
        ]
        update, letter = actions(client, finding)
        assert letter["status"] == "PENDING"

        post(client, f"/recommendations/{update['id']}/approve")
        stale = post(client, f"/recommendations/{cash[0]['id']}/approve")
        letter_now = post(client, f"/recommendations/{letter['id']}/approve")

    assert cash
    assert (stale.status_code, stale.json()["code"]) == (409, "STALE_RECOMMENDATION")
    assert letter_now.json()["recommendation"]["status"] == "APPROVED_EXECUTED"


def test_a_new_cash_run_leaves_proof_actions_pending() -> None:
    with client_for(base) as client:
        finding = upload_rent(client)
        version = client.get("/api/v1/company").json()["financial_version"]
        post(
            client,
            "/events",
            {
                "type": "CLOSURE_PLANNED",
                "expected_financial_version": version,
                "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"},
            },
        )
        statuses = [r["status"] for r in actions(client, finding)]
    assert statuses == ["PENDING", "PENDING"]


def test_policy_for_proof_actions() -> None:
    mandate = initial_mandate(demo_load_time(base.demo_as_of_date))
    paused = replace(mandate, paused=True)

    def lane(kind, category=Category.RENT, m=mandate, approved=False) -> Lane:
        return evaluate_proof_action(kind, category, "Main Street", m, approved).lane

    assert lane(RecommendationKind.KEEP_PAYMENT) is Lane.NEEDS_APPROVAL
    assert lane(RecommendationKind.UPDATE_RECURRING_AMOUNT) is Lane.NEEDS_APPROVAL
    assert lane(RecommendationKind.SEND_LETTER) is Lane.NEEDS_APPROVAL
    assert lane(RecommendationKind.UPDATE_RECURRING_AMOUNT, Category.PAYROLL) is Lane.NOT_ALLOWED
    assert lane(RecommendationKind.KEEP_PAYMENT, Category.TAX) is Lane.NOT_ALLOWED
    assert lane(RecommendationKind.SEND_LETTER, Category.PAYROLL) is Lane.NEEDS_APPROVAL
    assert lane(RecommendationKind.UPDATE_RECURRING_AMOUNT, approved=True) is (
        Lane.DONE_WITHIN_MANDATE
    )
    assert lane(RecommendationKind.SEND_LETTER, m=paused) is Lane.NEEDS_APPROVAL
    checks = evaluate_proof_action(
        RecommendationKind.SEND_LETTER, None, "Main Street", paused
    ).checks
    assert [(c.rule, c.result.value) for c in checks] == [("R7", "FAIL"), ("PAUSE", "FAIL")]
