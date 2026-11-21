"""B30, the release gate: the Phase 1 demo through the public API, in FALLBACK and AGENT mode,
with the numbers of docs/DESIGN.md (local/demo.sh is the manual version)."""

import hashlib
import time
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.agent import AgentUnavailableError
from api.audit import GENESIS_HASH
from api.config import Settings, get_settings
from api.main import create_app

TOKEN = "scenario-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")

S0 = {
    "first_breach": None,
    "lowest": {"date": "2025-06-01", "closing": "-39630"},
    "days_below_floor": 0,
    "end_balance": "239220",
}
S2 = {
    "first_breach": {"date": "2025-06-27", "closing": "-583291"},
    "lowest": {"date": "2025-08-02", "closing": "-2108111"},
    "days_below_floor": 38,
}
CHF_MOVE = "postpone Luigi Amalfi (Luigi's Ristorante Zurich) (4 333,34 €) to 30 Jul"
CREDIT = "a credit line of 20 000,00 € from 27 Jun"


class AgentService:
    """The AI service as the backend sees it: accepts runs (202) and leaves the work to the
    test, which calls the tools and completes like the model; the texts stay templates."""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []

    def start_run(self, payload: dict[str, Any]) -> None:
        self.started.append(payload)

    def explain(self, payload: dict[str, Any]):
        raise AgentUnavailableError("templates only")


def post(client: TestClient, path: str, body: dict | None = None):
    response = client.post(f"/api/v1{path}", json=body, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code < 300, response.text
    return response.json()


def summary(client: TestClient, keys=("first_breach", "lowest", "days_below_floor")) -> dict:
    data = client.get("/api/v1/forecast").json()["summary"]
    if data["first_breach"]:
        data["first_breach"] = {k: data["first_breach"][k] for k in ("date", "closing")}
    return {k: data[k] for k in keys}


def tool(client: TestClient, run_id: str, name: str, arguments: dict | None = None) -> dict:
    response = client.post(
        f"/internal/v1/tools/{name}",
        json={"run_id": run_id, "arguments": arguments or {}, "call_id": str(uuid4())},
        headers=AUTH,
    ).json()
    assert response["ok"], response["error"]
    return response["result"]


def act_like_the_model(client: TestClient, run_id: str) -> None:
    """The demo's agent: forecast, the planner's candidate, credit after the move, complete."""
    tool(client, run_id, "get_mandate")
    tool(client, run_id, "get_forecast")
    [candidate] = tool(client, run_id, "find_reschedule_options")["candidates"]
    move = {"outflow_id": candidate["outflow_id"], "to_date": candidate["to_date"]}
    line = tool(client, run_id, "size_credit_line", {"after_moves": [move]})["credit_line"]
    credit = {"amount": line["amount"], "available_from": line["available_from"]}
    response = client.post(
        f"/internal/v1/runs/{run_id}/complete",
        json={
            "proposals": [
                {"kind": "RESCHEDULE_PAYMENT", "move": move},
                {"kind": "CREDIT_LINE", "credit_line": credit},
            ],
            "summary_note": "Move the CHF transfer, then add credit.",
            "model": "vero-llm",
            "prompt_version": "scenario",
            "tool_calls": 4,
        },
        headers=AUTH,
    )
    assert response.status_code == 204, response.text


def finished(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 5
    while (run := client.get(f"/api/v1/runs/{run_id}").json())["status"] == "RUNNING":
        assert time.monotonic() < deadline, "run did not finish"
        time.sleep(0.02)
    return run


def chain_holds(log: list[dict]) -> bool:
    """The hash chain, recomputed with the formula in DATA_CONTRACTS (not the backend's code)."""
    previous = GENESIS_HASH
    for seq, e in enumerate(log, start=1):
        fields = [
            e["prev_hash"],
            str(e["seq"]),
            e["timestamp"],
            e["actor"],
            e["agent"] or "",
            e["run_id"] or "",
            e["recommendation_id"] or "",
            e["lane"] or "",
            e["execution"] or "",
            e["model"] or "",
            e["action"],
        ]
        digest = hashlib.sha256("|".join(fields).encode()).hexdigest()
        if e["seq"] != seq or e["prev_hash"] != previous or e["hash"] != digest:
            return False
        previous = e["hash"]
    return True


CASH_KINDS = ("RESCHEDULE_PAYMENT", "CREDIT_LINE")


def closure_story(
    client: TestClient, ai: AgentService, agent: bool, approve_credit: bool = True
) -> dict:
    """The Phase 1 demo's core with DESIGN.md's numbers: mandate v2, the closure 1–30 June,
    CHF-06 moved within the mandate, the 20 000 € credit line proposed (and approved).
    Returns the credit line recommendation."""
    v2 = {
        k: v
        for k, v in client.get("/api/v1/mandate").json().items()
        if k not in ("version", "status", "created_at", "activated_at", "paused")
    } | {"act_alone_enabled": True, "note": "Act alone within the limits"}
    assert post(client, "/mandate", v2)["version"] == 2
    assert post(client, "/mandate/2/activate", {"expected_active_version": 1})["status"] == (
        "ACTIVE"
    )

    version = client.get("/api/v1/company").json()["financial_version"]
    started = post(
        client,
        "/events",
        {
            "type": "CLOSURE_PLANNED",
            "expected_financial_version": version,
            "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"},
        },
    )
    if agent:
        assert started["status"] == "RUNNING"
        act_like_the_model(client, started["run_id"])
    run = finished(client, started["run_id"])
    assert (run["status"], run["mode"]) == ("SUCCEEDED", "AGENT" if agent else "FALLBACK")
    cash_runs = [p["run_id"] for p in ai.started if p.get("kind", "CASH") == "CASH"]
    assert cash_runs == ([started["run_id"]] if agent else [])

    recommendations = [
        r for r in client.get("/api/v1/recommendations").json() if r["kind"] in CASH_KINDS
    ]
    assert [(r["kind"], r["lane"], r["status"]) for r in recommendations] == [
        ("RESCHEDULE_PAYMENT", "DONE_WITHIN_MANDATE", "EXECUTED"),
        ("CREDIT_LINE", "NEEDS_APPROVAL", "PENDING"),
    ]
    move, credit = recommendations
    assert (move["subject"]["ref"], move["subject"]["to_date"]) == ("CHF-06", "2025-07-30")
    assert (credit["subject"]["amount"], credit["subject"]["available_from"]) == (
        "2000000",
        "2025-06-27",
    )
    assert summary(client) == S2
    if approve_credit:
        approved = post(client, f"/recommendations/{credit['id']}/approve")
        assert approved["recommendation"]["status"] == "APPROVED_EXECUTED"
        assert approved["forecast_summary"]["first_breach"] is None
        assert summary(client)["first_breach"] is None
    return credit


@pytest.mark.parametrize("mode", ["FALLBACK", "AGENT"])
def test_phase_1_demo_through_the_public_api(mode: str) -> None:
    agent = mode == "AGENT"
    ai = AgentService()
    settings = Settings(**(base.model_dump() | {"internal_token": TOKEN if agent else None}))
    with TestClient(create_app(settings, ai)) as client:
        post(client, "/demo/reset")
        assert summary(client, ("first_breach", "lowest", "days_below_floor", "end_balance")) == S0
        first_log = client.get("/api/v1/audit").json()
        assert len(first_log) == 1

        closure_story(client, ai, agent)

        payroll = next(
            o for o in client.get("/api/v1/outflows").json() if o["category"] == "PAYROLL"
        )
        request = post(
            client, f"/outflows/{payroll['id']}/reschedule-requests", {"to_date": "2025-07-20"}
        )
        finished(client, request["run_id"])
        blocked = [
            r
            for r in client.get("/api/v1/recommendations").json()
            if r["subject"].get("outflow_id") == payroll["id"]
        ]
        assert [(r["lane"], r["status"]) for r in blocked] == [("NOT_ALLOWED", "BLOCKED")]

        backtest = client.get("/api/v1/forecast/backtest").json()
        assert backtest["forecast"]["lowest"] == {"date": "2025-07-03", "closing": "-2276082"}
        assert backtest["forecast"]["days_below_floor"] == 50
        assert backtest["actual"]["first_breach"]["date"] == "2025-06-15"
        assert backtest["actual"]["first_breach"]["closing"] == "-884869"
        assert backtest["actual"]["lowest"] == {"date": "2025-07-03", "closing": "-2209760"}
        assert backtest["actual"]["days_below_floor"] == 48
        assert (backtest["lowest_error"], backtest["lowest_error_bp"]) == ("-66322", 300)

        log = client.get("/api/v1/audit").json()
        assert chain_holds(log)
        actions = [(e["actor"], e["action"]) for e in log]
        assert ("OWNER", f"Approved: {CREDIT} (simulated)") in actions
        assert ("RULES", f"Done within your mandate: {CHF_MOVE} (simulated)") in actions
        assert any(
            actor == "OWNER" and action.startswith("Asked to move ") for actor, action in actions
        )
        refused = [e for e in log if e["actor"] == "RULES" and e["lane"] == "NOT_ALLOWED"]
        assert len(refused) == 1
        assert refused[0]["action"].startswith("Not allowed by your rules: ")
        proposed = [e for e in log if e["action"].startswith("Proposed to ")]
        assert [e["actor"] for e in proposed] == ["AGENT" if agent else "SYSTEM"] * 2
        assert all(e["model"] == ("vero-llm" if agent else None) for e in proposed)

        post(client, "/demo/reset")
        assert summary(client, ("first_breach", "lowest", "days_below_floor", "end_balance")) == S0
        assert client.get("/api/v1/recommendations").json() == []
        assert client.get("/api/v1/mandate").json()["version"] == 1
        assert client.get("/api/v1/audit").json() == first_log
