from dataclasses import replace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from api.config import Settings
from api.domain.models import RunStatus
from api.main import create_app


def test_agent_can_read_only_current_active_run_forecast():
    token = str(uuid4())
    app = create_app(Settings(internal_token=SecretStr(token)))
    path = "/internal/v1/tools"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        client.headers["Authorization"] = f"Bearer {token}"
        assert [tool["name"] for tool in client.get(path).json()] == ["get_forecast"]
        event = {"type": "CLOSURE_PLANNED", "expected_financial_version": 1,
                 "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"}}
        run_id = UUID(client.post("/api/v1/events", json=event,
                                  headers={"Idempotency-Key": str(uuid4())}).json()["run_id"])
        call = {"run_id": str(run_id), "arguments": {}, "call_id": "forecast-1"}
        url = path + "/get_forecast"
        assert client.post(url, json=call).json()["error"]["code"] == "RUN_CLOSED"
        with app.state.repository.write() as state:
            state.runs[run_id] = replace(state.runs[run_id], status=RunStatus.RUNNING, finished_at=None)
        expected = client.get("/api/v1/forecast").json()
        response = client.post(url, json=call).json()
        assert response["result"] == {k: expected[k] for k in ("summary", "drivers", "planned_events")}
        assert client.post(url, json=call).json() == response
        assert response["tool_calls_used"] == 1
        with app.state.repository.write() as state:
            state.financial_version += 1
        assert client.post(url, json=call).json()["error"]["code"] == "STALE_RUN"
