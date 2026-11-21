import hashlib
import time
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.agent import AgentUnavailableError
from api.audit import GENESIS_HASH, broken_at
from api.config import Settings, get_settings
from api.explain import ExplainResult
from api.main import create_app

TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")

CHF_MOVE = "postpone Luigi Amalfi (Luigi's Ristorante Zurich) (4 333,34 €) to 30 Jul"
ACTS_ALONE = "Vero may act alone within your limits"
V2 = {
    "cash_floor": "-500000",
    "act_alone_enabled": True,
    "max_delay_days": 45,
    "max_amount_per_action": "500000",
    "max_amount_per_day": "500000",
    "protected_categories": ["PAYROLL", "SOCIAL_SECURITY", "TAX"],
    "credit_enabled": True,
    "max_credit_amount": "3000000",
}


class ScriptedAI:
    def __init__(self, available: bool = True) -> None:
        self.available = available

    def start_run(self, payload: dict[str, Any]) -> None:
        if not self.available:
            raise AgentUnavailableError("connection refused")

    def explain(self, payload: dict[str, Any]) -> ExplainResult:
        items = [
            {"recommendation_id": i["recommendation_id"], "text": "Model text"}
            for i in payload["items"]
        ]
        return ExplainResult(headline="Model headline", items=items, source="MODEL")


@pytest.fixture
def client():
    with TestClient(create_app(base)) as test_client:
        test_client.post("/api/v1/demo/reset")
        yield test_client


def post(client: TestClient, path: str, body: dict | None = None, key: str | None = None):
    return client.post(
        f"/api/v1{path}", json=body, headers={"Idempotency-Key": key or str(uuid4())}
    )


def entries(client: TestClient, after_seq: int = 0) -> list[dict]:
    response = client.get("/api/v1/audit", params={"after_seq": after_seq})
    assert response.status_code == 200
    return response.json()


def actions(log: list[dict]) -> list[tuple[str, str]]:
    return [(e["actor"], e["action"]) for e in log]


def close_june(client: TestClient):
    version = client.get("/api/v1/company").json()["financial_version"]
    return post(
        client,
        "/events",
        {
            "type": "CLOSURE_PLANNED",
            "expected_financial_version": version,
            "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"},
        },
    )


def activate_v2(client: TestClient) -> None:
    post(client, "/mandate", V2)
    post(client, "/mandate/2/activate", {"expected_active_version": 1})


def documented_hash(e: dict) -> str:
    """The formula from DATA_CONTRACTS, computed without the backend's code."""
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
    return hashlib.sha256("|".join(fields).encode()).hexdigest()


def assert_chain(log: list[dict]) -> None:
    previous = GENESIS_HASH
    for seq, entry in enumerate(log, start=1):
        assert entry["seq"] == seq
        assert entry["prev_hash"] == previous
        assert entry["hash"] == documented_hash(entry)
        previous = entry["hash"]


def test_reset_starts_a_new_log_with_one_entry(client: TestClient) -> None:
    close_june(client)
    assert len(entries(client)) > 1
    client.post("/api/v1/demo/reset")
    [first] = entries(client)
    assert first["actor"] == "SYSTEM"
    assert first["action"].startswith("Loaded Pizzeria Amalfi: ")
    assert first["action"].endswith("up to 04 May 2025, mandate v1 active")
    assert first["timestamp"] == "2025-05-05T08:00:00.000Z"
    assert first["prev_hash"] == GENESIS_HASH
    client.post("/api/v1/demo/reset")
    assert entries(client) == [first]


def test_demo_path_is_logged_and_sealed(client: TestClient) -> None:
    activate_v2(client)
    run_id = close_june(client).json()["run_id"]
    recommendations = client.get("/api/v1/recommendations").json()
    credit = next(r for r in recommendations if r["kind"] == "CREDIT_LINE")
    post(client, f"/recommendations/{credit['id']}/approve")
    log = entries(client)
    assert_chain(log)
    assert actions(log)[1:] == [
        ("OWNER", "Saved mandate v2 as a draft"),
        ("OWNER", f"Activated mandate v2: floor -5 000,00 €, {ACTS_ALONE}"),
        ("OWNER", "Planned a closure: 1 Jun – 30 Jun"),
        ("SYSTEM", f"Proposed to {CHF_MOVE}"),
        ("RULES", f"Done within your mandate: {CHF_MOVE} (simulated)"),
        ("SYSTEM", "Proposed to a credit line of 20 000,00 € from 27 Jun"),
        ("SYSTEM", log[7]["action"]),
        ("OWNER", "Approved: a credit line of 20 000,00 € from 27 Jun (simulated)"),
    ]
    assert log[7]["action"].startswith("You plan to close 1 Jun – 30 Jun: ")
    assert log[7]["run_id"] == run_id
    move, executed, credit_entry, approved = log[4], log[5], log[6], log[8]
    assert (move["lane"], move["execution"]) == ("DONE_WITHIN_MANDATE", None)
    assert (executed["lane"], executed["execution"]) == ("DONE_WITHIN_MANDATE", "SIMULATED")
    assert credit_entry["lane"] == "NEEDS_APPROVAL"
    assert approved["recommendation_id"] == credit["id"] == credit_entry["recommendation_id"]
    assert approved["execution"] == "SIMULATED"
    assert all(e["agent"] == "CASH" for e in log[3:])


def test_owner_rejection_and_blocked_payroll_request(client: TestClient) -> None:
    close_june(client)
    pending = client.get("/api/v1/recommendations", params={"status": "PENDING"}).json()
    post(client, f"/recommendations/{pending[0]['id']}/reject")
    payroll = next(o for o in client.get("/api/v1/outflows").json() if o["category"] == "PAYROLL")
    post(client, f"/outflows/{payroll['id']}/reschedule-requests", {"to_date": "2025-07-15"})
    log = entries(client)
    assert_chain(log)
    rejected = next(e for e in log if e["action"].startswith("Rejected: "))
    assert (rejected["actor"], rejected["recommendation_id"]) == ("OWNER", pending[0]["id"])
    asked, blocked = log[-3], log[-2]
    assert asked["actor"] == "OWNER" and asked["action"].startswith("Asked to move ")
    assert blocked["actor"] == "RULES" and blocked["lane"] == "NOT_ALLOWED"
    assert blocked["action"].startswith("Not allowed by your rules: ")


def test_replayed_request_adds_no_entry(client: TestClient) -> None:
    key = str(uuid4())
    post(client, "/mandate", V2, key)
    count = len(entries(client))
    post(client, "/mandate", V2, key)
    assert len(entries(client)) == count


def test_stale_decision_is_logged(client: TestClient) -> None:
    close_june(client)
    pending = client.get("/api/v1/recommendations", params={"status": "PENDING"}).json()
    with client.app.state.repository.write() as state:
        state.financial_version += 1
    response = post(client, f"/recommendations/{pending[0]['id']}/approve")
    assert response.status_code == 409
    last = entries(client)[-1]
    assert last["actor"] == "SYSTEM"
    assert last["action"].startswith("Could not apply your decision to ")


def test_after_seq_returns_only_newer_entries(client: TestClient) -> None:
    close_june(client)
    log = entries(client)
    assert entries(client, after_seq=2) == log[2:]
    assert client.get("/api/v1/audit", params={"after_seq": -1}).status_code == 422


def test_a_changed_entry_breaks_the_chain(client: TestClient) -> None:
    close_june(client)
    with client.app.state.repository.read() as state:
        log = list(state.audit)
    assert broken_at(log) is None
    tampered = [*log[:2], replace(log[2], action="Nothing happened"), *log[3:]]
    assert broken_at(tampered) == 3
    assert broken_at(log[:2] + log[3:]) == 4


def test_agent_run_is_logged_by_the_agent_with_its_model() -> None:
    agent_settings = Settings(**(base.model_dump() | {"internal_token": TOKEN}))
    with TestClient(create_app(agent_settings, ScriptedAI())) as client:
        client.post("/api/v1/demo/reset")
        activate_v2(client)
        run_id = close_june(client).json()["run_id"]

        def call(name: str, arguments: dict | None = None) -> dict:
            return client.post(
                f"/internal/v1/tools/{name}",
                json={"run_id": run_id, "arguments": arguments or {}, "call_id": str(uuid4())},
                headers=AUTH,
            ).json()["result"]

        call("get_forecast")
        [candidate] = call("find_reschedule_options")["candidates"]
        move = {"outflow_id": candidate["outflow_id"], "to_date": candidate["to_date"]}
        line = call("size_credit_line", {"after_moves": [move]})["credit_line"]
        client.post(
            f"/internal/v1/runs/{run_id}/complete",
            json={
                "proposals": [
                    {"kind": "RESCHEDULE_PAYMENT", "move": move},
                    {
                        "kind": "CREDIT_LINE",
                        "credit_line": {
                            "amount": line["amount"],
                            "available_from": line["available_from"],
                        },
                    },
                ],
                "summary_note": "Move the CHF transfer, then add credit.",
                "model": "vero-llm",
                "prompt_version": "p1",
                "tool_calls": 3,
            },
            headers=AUTH,
        )
        deadline = time.monotonic() + 5
        while client.get(f"/api/v1/runs/{run_id}").json()["status"] == "RUNNING":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        log = entries(client)
    assert_chain(log)
    proposals = [e for e in log if e["action"].startswith("Proposed to ")]
    assert [(e["actor"], e["model"]) for e in proposals] == [("AGENT", "vero-llm")] * 2
    assert (log[-1]["actor"], log[-1]["action"], log[-1]["model"]) == (
        "AGENT",
        "Model headline",
        "vero-llm",
    )


def test_unreachable_agent_fallback_is_logged() -> None:
    agent_settings = Settings(**(base.model_dump() | {"internal_token": TOKEN}))
    with TestClient(create_app(agent_settings, ScriptedAI(available=False))) as client:
        client.post("/api/v1/demo/reset")
        close_june(client)
        log = entries(client)
    assert_chain(log)
    fallback = next(e for e in log if e["action"].startswith("The AI service is unavailable"))
    assert fallback["actor"] == "SYSTEM"
    assert [e["actor"] for e in log if e["action"].startswith("Proposed to ")] == ["SYSTEM"] * 2
