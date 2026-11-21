"""P19, the Vero Proof release gate: the Proof demo through the public API in FALLBACK and AGENT
mode with the numbers of docs/PROOF.md §7, then the Phase 1 closure story in the fixed demo order
(Proof first: the hero keeps the rent, so nothing in Vero Cash changes)."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_document_upload import FakeAI
from test_investigations import ANSWER, HERO_CALLS
from test_proof_actions import RENT_PDF, vpi_above_threshold
from test_reset import snapshot
from test_scenario import (
    AUTH,
    S0,
    TOKEN,
    chain_holds,
    closure_story,
    finished,
    post,
    summary,
)

from api.config import Settings, get_settings
from api.main import create_app

base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")
FULL = ("first_breach", "lowest", "days_below_floor", "end_balance")
LETTER = "Ihre Mietanpassung ab 01.06.2025 – Bitte um Erläuterung gemäß §4.2 Mietvertrag"
QUIET = ["ASIMEX", "FATTORIA", "ASIMEX"]


class ScenarioAI(FakeAI):
    """The AI service for both phases: reads the demo PDFs correctly and accepts agent runs,
    whose work the test does through the internal API; texts stay templates."""

    def __init__(self) -> None:
        super().__init__()
        self.started: list[dict[str, Any]] = []

    def start_run(self, payload: dict[str, Any]) -> None:
        self.started.append(payload)


def app_for(agent: bool, settings: Settings = base) -> tuple[TestClient, ScenarioAI]:
    ai = ScenarioAI()
    changes = {"internal_token": TOKEN if agent else None, "investigation_agent_enabled": True}
    return TestClient(create_app(Settings(**(settings.model_dump() | changes)), ai)), ai


def counts(client: TestClient) -> tuple[int, int, int, int]:
    c = client.get("/api/v1/digest").json()["counts"]
    return c["changes"], c["handled_quietly"], c["needs_owner"], c["routine_checks"]


def investigate_like_the_model(client: TestClient, run_id: str, current_index: int) -> None:
    """PROOF §7.1's four calls (with the index the dataset holds), then submit_finding."""
    for call_id, name, arguments in HERO_CALLS:
        if name == "compute":
            arguments = arguments | {
                "inputs": arguments["inputs"] | {"current_index": current_index}
            }
        response = client.post(
            f"/internal/v1/tools/{name}",
            json={"run_id": run_id, "arguments": arguments, "call_id": call_id},
            headers=AUTH,
        ).json()
        assert response["ok"], response["error"]
    response = client.post(f"/internal/v1/runs/{run_id}/complete", json=ANSWER, headers=AUTH)
    assert response.status_code == 204, response.text


def upload_the_rent_adjustment(client: TestClient, agent: bool, current_index: int = 1212):
    """The live upload: the run reads, checks and investigates; returns the run and finding."""
    accepted = post_file(client)
    if agent:
        assert accepted["status"] == "RUNNING"
        investigate_like_the_model(client, accepted["run_id"], current_index)
    run = finished(client, accepted["run_id"])
    [finding_id] = run["finding_ids"]
    return run, client.get(f"/api/v1/findings/{finding_id}").json()


def post_file(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("Mietanpassung.pdf", RENT_PDF, "application/pdf")},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


def actions_of(client: TestClient, finding: dict) -> list[dict]:
    by_id = {r["id"]: r for r in client.get("/api/v1/recommendations").json()}
    return [by_id[i] for i in finding["recommendation_ids"]]


@pytest.mark.parametrize("mode", ["FALLBACK", "AGENT"])
def test_proof_demo_then_the_closure_story(mode: str) -> None:
    agent = mode == "AGENT"
    client, ai = app_for(agent)
    with client:
        post(client, "/demo/reset")
        fresh = snapshot(client)
        digest = client.get("/api/v1/digest").json()
        assert counts(client) == (3, 3, 0, 3)
        assert [c["cost_ref"] for c in digest["changes"]] == QUIET
        assert digest["changes"][2]["investigation_mode"] == "FALLBACK"
        assert [c["cost_ref"] for c in digest["routine_checks"]] == ["RENT", "UTIL", "XFER"]

        run, finding = upload_the_rent_adjustment(client, agent)
        kinds = [step["kind"] for step in run["trace"]]
        assert (run["kind"], run["status"], run["mode"]) == ("PROOF", "SUCCEEDED", mode)
        assert kinds[0] == "RECEIVE" and kinds[-1] == "SUMMARY"
        for kind in ("CHECK", "INVESTIGATE", "GROUND", "PROPAGATE", "POLICY"):
            assert kind in kinds, (kind, kinds)
        assert ("EXTRACT" in kinds) is agent
        assert [p["kind"] for p in ai.started] == (["PROOF"] if agent else [])
        assert len(ai.extracted) == (1 if agent else 0)

        assert (finding["classification"], finding["status"], finding["escalated"]) == (
            "CONFLICT",
            "PENDING_APPROVAL",
            False,
        )
        assert finding["impact"] | {"currency": "EUR"} == {
            "currency": "EUR",
            "expected": "380000",
            "billed": "410400",
            "delta": "30400",
            "bps": 800,
        }
        investigation = finding["investigation"]
        assert investigation["mode"] == mode
        assert (investigation["formula_id"], investigation["outputs"]["change_bps"]) == (
            "vpi_threshold_indexation",
            306,
        )
        assert (investigation["justified"], investigation["disputed"]) == ("380000", "30400")
        propagation = finding["propagation"]
        assert (propagation["decision"], propagation["current_amount"]) == ("KEEP", "380000")
        assert (
            propagation["disputed"],
            propagation["disputed_annual"],
            propagation["disputed_in_window"],
        ) == ("30400", "364800", "91200")

        keep, letter = actions_of(client, finding)
        assert [(a["kind"], a["lane"], a["status"]) for a in (keep, letter)] == [
            ("KEEP_PAYMENT", "NEEDS_APPROVAL", "PENDING"),
            ("SEND_LETTER", "NEEDS_APPROVAL", "PENDING"),
        ]
        assert (keep["subject"]["amount"], keep["subject"]["billed_amount"]) == (
            "380000",
            "410400",
        )
        assert letter["subject"]["letter_subject"] == LETTER
        assert letter["subject"]["draft_source"] == "TEMPLATE"
        assert counts(client) == (4, 3, 1, 3)
        hero = client.get("/api/v1/digest").json()["changes"][-1]
        assert [a["recommendation_id"] for a in hero["actions"]] == [keep["id"], letter["id"]]
        assert summary(client, FULL) == S0

        for action in (keep, letter):
            decided = post(client, f"/recommendations/{action['id']}/approve")
            assert decided["recommendation"]["status"] == "APPROVED_EXECUTED"
            receipts = [
                client.get(f"/api/v1/receipts/{i}").json()
                for i in decided["recommendation"]["receipt_ids"]
            ]
            assert [(r["kind"], r["decision"]) for r in receipts] == [
                ("PROPOSAL", None),
                ("DECISION", "APPROVED"),
            ]
        resolved = client.get(f"/api/v1/findings/{finding['id']}").json()
        assert resolved["status"] == "RESOLVED"
        assert counts(client) == (3, 3, 0, 3)
        assert summary(client, FULL) == S0

        closure_story(client, ai, agent)
        assert [p["kind"] for p in ai.started] == (["PROOF", "CASH"] if agent else [])

        log = client.get("/api/v1/audit").json()
        assert chain_holds(log)
        owner = [e["action"] for e in log if e["actor"] == "OWNER" and e["agent"] == "PROOF"]
        assert owner == [
            "Uploaded Mietanpassung.pdf",
            "Approved: keep paying 3 800,00 € for RENT (simulated)",
            "Approved: send a letter to Main Street Real Estate GmbH (simulated)",
        ]

        post(client, "/demo/reset")
        assert snapshot(client) == fresh


@pytest.mark.parametrize("mode", ["FALLBACK", "AGENT"])
def test_an_amount_change_after_the_closure_makes_the_credit_line_stale(
    mode: str, tmp_path: Path
) -> None:
    agent = mode == "AGENT"
    client, ai = app_for(agent, vpi_above_threshold(tmp_path))
    with client:
        post(client, "/demo/reset")
        credit = closure_story(client, ai, agent, approve_credit=False)

        run, finding = upload_the_rent_adjustment(client, agent, current_index=1236)
        assert (run["status"], run["mode"]) == ("SUCCEEDED", mode)
        assert (finding["classification"], finding["investigation"]["justified"]) == (
            "CONFLICT",
            "399388",
        )
        update, letter = actions_of(client, finding)
        assert [(a["kind"], a["status"]) for a in (update, letter)] == [
            ("UPDATE_RECURRING_AMOUNT", "PENDING"),
            ("SEND_LETTER", "PENDING"),
        ]
        assert (update["subject"]["from_amount"], update["subject"]["to_amount"]) == (
            "380000",
            "399388",
        )
        listed = {r["id"]: r for r in client.get("/api/v1/recommendations").json()}
        assert listed[credit["id"]]["status"] == "PENDING"

        version = client.get("/api/v1/company").json()["financial_version"]
        post(client, f"/recommendations/{update['id']}/approve")
        assert client.get("/api/v1/company").json()["financial_version"] == version + 1
        refused = client.post(
            f"/api/v1/recommendations/{credit['id']}/approve",
            headers={"Idempotency-Key": str(uuid4())},
        )

        assert (refused.status_code, refused.json()["code"]) == (409, "STALE_RECOMMENDATION")
        listed = {r["id"]: r for r in client.get("/api/v1/recommendations").json()}
        assert listed[credit["id"]]["status"] == "STALE"
        assert listed[letter["id"]]["status"] == "PENDING"
        rent = [
            o["amount"]
            for o in client.get("/api/v1/outflows").json()
            if o["category"] == "RENT" and o["scheduled_date"] >= "2025-06-01"
        ]
        assert rent == ["399388"] * 3
