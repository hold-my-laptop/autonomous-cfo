import time
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.agent import AgentUnavailableError
from api.config import Settings, get_settings
from api.main import create_app

TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")

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


class FakeAI:
    """Accepts runs like the AI service (202) and leaves the work to the test."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.started: list[dict[str, Any]] = []

    def start_run(self, payload: dict[str, Any]) -> None:
        if not self.available:
            raise AgentUnavailableError("connection refused")
        self.started.append(payload)


def settings(**changes) -> Settings:
    return Settings(**(base.model_dump() | {"internal_token": TOKEN} | changes))


@pytest.fixture
def ai() -> FakeAI:
    return FakeAI()


@pytest.fixture
def client(ai: FakeAI):
    with TestClient(create_app(settings(), ai)) as test_client:
        yield test_client


def post(client: TestClient, path: str, body: dict | None = None):
    return client.post(f"/api/v1{path}", json=body, headers={"Idempotency-Key": str(uuid4())})


def activate_v2(client: TestClient) -> None:
    post(client, "/mandate", V2)
    post(client, "/mandate/2/activate", {"expected_active_version": 1})


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


def run(client: TestClient, run_id: str) -> dict:
    return client.get(f"/api/v1/runs/{run_id}").json()


def tool(client: TestClient, run_id: str, name: str, arguments: dict | None = None) -> dict:
    return client.post(
        f"/internal/v1/tools/{name}",
        json={"run_id": run_id, "arguments": arguments or {}, "call_id": str(uuid4())},
        headers=AUTH,
    ).json()["result"]


def callback(client: TestClient, run_id: str, action: str, body: dict):
    return client.post(f"/internal/v1/runs/{run_id}/{action}", json=body, headers=AUTH)


def agent_work(client: TestClient, run_id: str) -> tuple[dict, dict]:
    """What the model does in the demo: forecast, planner candidates, credit after the move."""
    tool(client, run_id, "get_forecast")
    [candidate] = tool(client, run_id, "find_reschedule_options")["candidates"]
    move = {"outflow_id": candidate["outflow_id"], "to_date": candidate["to_date"]}
    line = tool(client, run_id, "size_credit_line", {"after_moves": [move]})["credit_line"]
    return move, {"amount": line["amount"], "available_from": line["available_from"]}


def completion(proposals: list[dict]) -> dict:
    return {
        "proposals": proposals,
        "summary_note": "Closing in June empties the account; move the CHF transfer, add credit.",
        "model": "granite-3.3-8b-instruct",
        "prompt_version": "p1",
        "tool_calls": 3,
    }


def wait_until_finished(client: TestClient, run_id: str, seconds: float = 5) -> dict:
    deadline = time.monotonic() + seconds
    while (current := run(client, run_id))["status"] == "RUNNING":
        assert time.monotonic() < deadline, "run did not finish"
        time.sleep(0.05)
    return current


def test_event_starts_an_agent_run(client: TestClient, ai: FakeAI) -> None:
    response = close_june(client)

    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert response.json()["status"] == "RUNNING"
    assert (run(client, run_id)["status"], run(client, run_id)["mode"]) == ("RUNNING", "AGENT")
    [payload] = ai.started
    assert payload["run_id"] == run_id
    assert payload["event"]["type"] == "CLOSURE_PLANNED"
    assert payload["event"]["payload"]["date_from"] == "2025-06-01"
    assert payload["company"]["as_of_date"] == "2025-05-05"
    assert payload["limits"] == {"max_tool_calls": 8, "deadline_s": 60}


def test_agent_path(client: TestClient) -> None:
    activate_v2(client)
    run_id = close_june(client).json()["run_id"]
    move, line = agent_work(client, run_id)
    assert callback(client, run_id, "notes", {"text": "June has no revenue."}).status_code == 204

    response = callback(
        client,
        run_id,
        "complete",
        completion(
            [
                {"kind": "RESCHEDULE_PAYMENT", "move": move, "rationale": "Delays the breach"},
                {"kind": "CREDIT_LINE", "credit_line": line},
            ]
        ),
    )

    assert response.status_code == 204
    finished = run(client, run_id)
    assert (finished["status"], finished["mode"]) == ("SUCCEEDED", "AGENT")
    assert finished["result"]["counts"] == {
        "done_within_mandate": 1,
        "needs_approval": 1,
        "not_allowed": 0,
    }
    agent_steps = [s["label"] for s in finished["trace"] if s["source"] == "AGENT"]
    assert agent_steps[0] == "June has no revenue."
    assert agent_steps[1].startswith("Closing in June")
    summary = finished["trace"][-1]
    assert summary["kind"] == "SUMMARY"
    assert summary["data"]["model"] == "granite-3.3-8b-instruct"
    assert client.get("/api/v1/forecast").json()["summary"]["first_breach"]["date"] == "2025-06-27"
    credit = client.get("/api/v1/overview").json()["lanes"]["NEEDS_APPROVAL"][0]
    assert (credit["subject"]["amount"], credit["subject"]["available_from"]) == (
        "2000000",
        "2025-06-27",
    )
    approved = post(client, f"/recommendations/{credit['id']}/approve")
    assert approved.json()["forecast_summary"]["first_breach"] is None


def test_invalid_proposals_are_discarded(client: TestClient) -> None:
    activate_v2(client)
    run_id = close_june(client).json()["run_id"]
    move, line = agent_work(client, run_id)
    payroll = next(o for o in client.get("/api/v1/outflows").json() if o["ref"] == "PAY-06")

    callback(
        client,
        run_id,
        "complete",
        completion(
            [
                {
                    "kind": "RESCHEDULE_PAYMENT",
                    "move": {"outflow_id": payroll["id"], "to_date": "2025-07-20"},
                },
                {"kind": "RESCHEDULE_PAYMENT", "move": move | {"to_date": "2025-07-31"}},
                {"kind": "CREDIT_LINE", "credit_line": line | {"amount": "5000000"}},
                {"kind": "TRANSFER_MONEY"},
                {"kind": "RESCHEDULE_PAYMENT", "move": move},
            ]
        ),
    )

    finished = run(client, run_id)
    assert finished["mode"] == "AGENT"
    discarded = [s["label"] for s in finished["trace"] if s["kind"] == "DISCARDED"]
    assert discarded == [
        "Proposal 1 ignored: not a planner candidate",
        "Proposal 2 ignored: not a planner candidate",
        "Proposal 3 ignored: credit line was not sized",
        "Proposal 4 ignored: unknown kind",
    ]
    assert finished["result"]["counts"]["done_within_mandate"] == 1
    assert [r["subject"]["ref"] for r in client.get("/api/v1/recommendations").json()] == ["CHF-06"]
    pay = next(o for o in client.get("/api/v1/outflows").json() if o["ref"] == "PAY-06")
    assert pay["scheduled_date"] == payroll["scheduled_date"]


def test_timeout_falls_back() -> None:
    with TestClient(create_app(settings(agent_deadline_s=0.3), FakeAI())) as client:
        activate_v2(client)
        run_id = close_june(client).json()["run_id"]
        finished = wait_until_finished(client, run_id)

        assert (finished["status"], finished["mode"]) == ("SUCCEEDED", "FALLBACK")
        [step] = [s for s in finished["trace"] if s["kind"] == "FALLBACK"]
        assert "deadline passed" in step["label"]
        assert finished["result"]["counts"]["done_within_mandate"] == 1

        late = callback(client, run_id, "complete", completion([]))
        assert (late.status_code, late.json()["code"]) == (409, "RUN_CLOSED")
        assert callback(client, run_id, "notes", {"text": "late"}).status_code == 409


def test_fail_falls_back(client: TestClient) -> None:
    run_id = close_june(client).json()["run_id"]

    response = callback(client, run_id, "fail", {"reason": "MODEL_ERROR", "detail": "500"})

    assert response.status_code == 204
    finished = run(client, run_id)
    assert (finished["status"], finished["mode"]) == ("SUCCEEDED", "FALLBACK")
    assert finished["result"]["counts"]["needs_approval"] == 2
    assert callback(client, run_id, "fail", {"reason": "again"}).status_code == 409


def test_unreachable_ai_falls_back() -> None:
    with TestClient(create_app(settings(), FakeAI(available=False))) as client:
        run_id = close_june(client).json()["run_id"]
        finished = run(client, run_id)

        assert (finished["status"], finished["mode"]) == ("SUCCEEDED", "FALLBACK")
        assert any("AI service unreachable" in s["label"] for s in finished["trace"])


def test_real_client_without_ai_service_falls_back() -> None:
    unreachable = settings(ai_url="http://127.0.0.1:9", agent_start_timeout_s=1.0)
    with TestClient(create_app(unreachable)) as client:
        run_id = close_june(client).json()["run_id"]
        assert run(client, run_id)["mode"] == "FALLBACK"


def test_one_agent_run_at_a_time(client: TestClient, ai: FakeAI) -> None:
    close_june(client)
    version = client.get("/api/v1/company").json()["financial_version"]
    second = post(
        client,
        "/events",
        {
            "type": "CLOSURE_PLANNED",
            "expected_financial_version": version,
            "payload": {"date_from": "2025-07-20", "date_to": "2025-07-21"},
        },
    )
    assert (second.status_code, second.json()["code"]) == (409, "RUN_IN_PROGRESS")
    assert len(ai.started) == 1


def test_replayed_event_does_not_start_the_agent_twice(client: TestClient, ai: FakeAI) -> None:
    body = {
        "type": "CLOSURE_PLANNED",
        "expected_financial_version": 1,
        "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"},
    }
    headers = {"Idempotency-Key": str(uuid4())}
    first = client.post("/api/v1/events", json=body, headers=headers)
    again = client.post("/api/v1/events", json=body, headers=headers)

    assert again.json() == first.json()
    assert len(ai.started) == 1


def test_data_change_during_run_falls_back(client: TestClient) -> None:
    run_id = close_june(client).json()["run_id"]
    agent_work(client, run_id)
    with client.app.state.repository.write() as state:
        state.financial_version += 1

    callback(client, run_id, "complete", completion([]))

    finished = run(client, run_id)
    assert finished["mode"] == "FALLBACK"
    assert any("data changed" in s["label"] for s in finished["trace"])


def test_callbacks_need_the_token(client: TestClient) -> None:
    run_id = close_june(client).json()["run_id"]
    response = client.post(f"/internal/v1/runs/{run_id}/fail", json={"reason": "x"})
    assert response.status_code == 401
    assert run(client, run_id)["status"] == "RUNNING"
