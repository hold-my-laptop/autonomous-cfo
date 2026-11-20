from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.domain.models import EventType, Run, RunMode, RunStatus
from api.main import create_app

TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")


def settings(**changes) -> Settings:
    return Settings(**(base.model_dump() | {"internal_token": TOKEN} | changes))


@pytest.fixture
def client():
    with TestClient(create_app(settings())) as test_client:
        yield test_client


def start_run(client: TestClient, close_june: bool = True, act_alone: bool = True) -> str:
    """A RUNNING agent run, as B23 will create after an event."""
    headers = {"Idempotency-Key": str(uuid4())}
    if act_alone:
        client.post(
            "/api/v1/mandate",
            json={
                "cash_floor": "-500000",
                "act_alone_enabled": True,
                "max_delay_days": 45,
                "max_amount_per_action": "500000",
                "max_amount_per_day": "500000",
                "protected_categories": ["PAYROLL", "SOCIAL_SECURITY", "TAX"],
                "credit_enabled": True,
                "max_credit_amount": "3000000",
            },
            headers=headers,
        )
        client.post(
            "/api/v1/mandate/2/activate",
            json={"expected_active_version": 1},
            headers={"Idempotency-Key": str(uuid4())},
        )
    run_id = uuid4()
    with client.app.state.repository.write() as state:
        if close_june:
            from api.domain.models import PlannedEvent, PlannedEventKind

            state.planned_events.append(
                PlannedEvent(
                    uuid4(),
                    PlannedEventKind.CLOSURE,
                    datetime(2025, 6, 1).date(),
                    datetime(2025, 6, 30).date(),
                )
            )
            state.financial_version += 1
        state.runs[run_id] = Run(
            id=run_id,
            event_id=uuid4(),
            trigger_type=EventType.CLOSURE_PLANNED,
            trigger_label="You plan to close 1 Jun – 30 Jun",
            status=RunStatus.RUNNING,
            mode=RunMode.AGENT,
            created_at=datetime.now(UTC),
            started_financial_version=state.financial_version,
        )
    return str(run_id)


def call(client: TestClient, run_id: str, name: str, arguments=None, call_id=None):
    response = client.post(
        f"/internal/v1/tools/{name}",
        json={"run_id": run_id, "arguments": arguments or {}, "call_id": call_id or str(uuid4())},
        headers=AUTH,
    )
    assert response.status_code == 200
    return response.json()


def test_requires_the_internal_token(client: TestClient) -> None:
    assert client.get("/internal/v1/tools").status_code == 401
    wrong = client.get("/internal/v1/tools", headers={"Authorization": "Bearer nope"})
    assert (wrong.status_code, wrong.json()["code"]) == (401, "UNAUTHORIZED")
    assert client.get("/internal/v1/tools", headers=AUTH).status_code == 200


def test_disabled_without_configured_token() -> None:
    with TestClient(create_app(settings(internal_token=None))) as client:
        assert client.get("/internal/v1/tools", headers=AUTH).status_code == 401


def test_internal_api_is_not_in_public_openapi(client: TestClient) -> None:
    assert not [p for p in client.get("/openapi.json").json()["paths"] if "internal" in p]


def test_tool_specs(client: TestClient) -> None:
    specs = client.get("/internal/v1/tools", headers=AUTH).json()

    assert [s["name"] for s in specs] == [
        "get_mandate",
        "get_forecast",
        "explain_drivers",
        "get_revenue_model",
        "list_outflows",
        "find_reschedule_options",
        "simulate_moves",
        "size_credit_line",
        "get_fx_exposure",
        "check_policy",
    ]
    assert all(s["description"] and s["input_schema"]["type"] == "object" for s in specs)
    assert "$defs" not in str(specs)


def test_agent_sequence_from_architecture(client: TestClient) -> None:
    run_id = start_run(client)

    forecast = call(client, run_id, "get_forecast")
    assert forecast["ok"]
    summary = forecast["result"]["summary"]
    assert summary["first_breach"]["date"] == "2025-06-15"
    assert summary["lowest"] == {"date": "2025-07-03", "closing": "-2276082"}
    assert forecast["result"]["drivers"][0]["kind"] == "NO_REVENUE"

    plan = call(client, run_id, "find_reschedule_options")["result"]
    [candidate] = plan["candidates"]
    assert (candidate["ref"], candidate["to_date"], candidate["amount"]) == (
        "CHF-06",
        "2025-07-30",
        "433334",
    )
    assert plan["resolved"] is False
    move = {"outflow_id": candidate["outflow_id"], "to_date": candidate["to_date"]}

    credit = call(client, run_id, "size_credit_line", {"after_moves": [move]})["result"]
    assert credit["needed"]
    assert credit["credit_line"]["amount"] == "2000000"
    assert credit["credit_line"]["available_from"] == "2025-06-27"
    assert credit["credit_line"]["headroom_at_lowest"] == "391889"

    preview = call(
        client, run_id, "check_policy", {"proposal": {"kind": "RESCHEDULE_PAYMENT", "move": move}}
    )
    assert preview["result"]["lane_preview"] == "DONE_WITHIN_MANDATE"
    credit_preview = call(
        client,
        run_id,
        "check_policy",
        {
            "proposal": {
                "kind": "CREDIT_LINE",
                "credit_line": {"amount": "2000000", "available_from": "2025-06-27"},
            }
        },
    )
    assert credit_preview["result"]["lane_preview"] == "NEEDS_APPROVAL"
    assert credit_preview["tool_calls_used"] == 5

    simulated = call(client, run_id, "simulate_moves", {"moves": [move]})["result"]["summary"]
    assert simulated["first_breach"]["date"] == "2025-06-27"

    run = client.get(f"/api/v1/runs/{run_id}").json()
    labels = [step["label"] for step in run["trace"]]
    assert labels[0] == "Recomputing your cash for the next 91 days"
    assert "Found 1 payment worth postponing" in labels
    assert all(step["source"] == "BACKEND" for step in run["trace"])
    assert client.get("/api/v1/company").json()["financial_version"] == 2


def test_other_read_tools(client: TestClient) -> None:
    run_id = start_run(client)

    assert call(client, run_id, "get_mandate")["result"]["act_alone_enabled"] is True
    assert call(client, run_id, "get_revenue_model")["result"]["weeks"] == 8
    movable = call(client, run_id, "list_outflows", {"movable_only": True})["result"]
    assert {o["ref"] for o in movable} == {"CHF-05", "CHF-06", "CHF-07"}
    around = call(client, run_id, "explain_drivers", {"date": "2025-07-03"})["result"]
    assert around["closed"] is False
    assert {o["ref"] for o in around["outflows"]} >= {"PAY-06", "RENT-07"}
    fx = call(client, run_id, "get_fx_exposure")["result"]
    assert (fx["currency"], fx["tranches_paid"], fx["eur_min"], fx["eur_max"]) == (
        "CHF",
        4,
        "432500",
        "435417",
    )
    assert len(fx["upcoming"]) == 3


def test_call_id_is_idempotent(client: TestClient) -> None:
    run_id = start_run(client)
    first = call(client, run_id, "get_forecast", call_id="call-1")
    again = call(client, run_id, "get_forecast", call_id="call-1")

    assert again == first
    assert call(client, run_id, "get_mandate")["tool_calls_used"] == 2


def test_errors_come_back_as_ok_false(client: TestClient) -> None:
    run_id = start_run(client)

    unknown = call(client, run_id, "move_money")
    assert (unknown["ok"], unknown["error"]["code"]) == (False, "UNKNOWN_TOOL")
    invalid = call(client, run_id, "explain_drivers", {"date": "tomorrow"})
    assert invalid["error"]["code"] == "INVALID_ARGUMENT"
    outside = call(client, run_id, "explain_drivers", {"date": "2026-01-01"})
    assert outside["error"]["code"] == "INVALID_ARGUMENT"
    extra = call(client, run_id, "get_forecast", {"surprise": True})
    assert extra["error"]["code"] == "INVALID_ARGUMENT"
    invented = call(
        client,
        run_id,
        "simulate_moves",
        {"moves": [{"outflow_id": str(uuid4()), "to_date": "2025-07-30"}]},
    )
    assert invented["error"]["code"] == "INVALID_ARGUMENT"
    assert "find_reschedule_options" in invented["error"]["message"]


def test_tool_limit(client: TestClient) -> None:
    with TestClient(create_app(settings(agent_max_tool_calls=2))) as limited:
        run_id = start_run(limited, act_alone=False)
        call(limited, run_id, "get_forecast")
        call(limited, run_id, "get_mandate")
        assert call(limited, run_id, "get_forecast")["error"]["code"] == "TOOL_LIMIT_REACHED"


def test_stale_and_closed_runs(client: TestClient) -> None:
    run_id = start_run(client)
    with client.app.state.repository.write() as state:
        state.financial_version += 1
    assert call(client, run_id, "get_forecast")["error"]["code"] == "STALE_RUN"
    assert call(client, str(uuid4()), "get_forecast")["error"]["code"] == "RUN_CLOSED"

    finished = client.post(
        "/api/v1/events",
        json={
            "type": "CLOSURE_PLANNED",
            "expected_financial_version": client.get("/api/v1/company").json()["financial_version"],
            "payload": {"date_from": "2025-07-20", "date_to": "2025-07-21"},
        },
        headers={"Idempotency-Key": str(uuid4())},
    ).json()["run_id"]
    assert call(client, finished, "get_forecast")["error"]["code"] == "RUN_CLOSED"


def test_tools_never_change_financial_data(client: TestClient) -> None:
    run_id = start_run(client)
    before = client.get("/api/v1/forecast").json()["summary"]
    for name in ("get_forecast", "find_reschedule_options", "size_credit_line"):
        call(client, run_id, name)
    assert client.get("/api/v1/forecast").json()["summary"] == before
    assert client.get("/api/v1/recommendations").json() == []
