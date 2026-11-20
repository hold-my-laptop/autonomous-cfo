from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import create_app

settings = get_settings()
pytestmark = pytest.mark.skipif(
    not settings.statement_path.exists(),
    reason=f"sample CSV not found at {settings.statement_path}",
)


@pytest.fixture
def client():
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def closure(date_from="2025-06-01", date_to="2025-06-30", version=1, **payload) -> dict:
    return {
        "type": "CLOSURE_PLANNED",
        "expected_financial_version": version,
        "payload": {"date_from": date_from, "date_to": date_to, **payload},
    }


def post_event(client: TestClient, body: dict, key: str | None = None):
    return client.post(
        "/api/v1/events", json=body, headers={"Idempotency-Key": key or str(uuid4())}
    )


def test_june_closure_gives_s1_forecast(client: TestClient) -> None:
    response = post_event(client, closure(note="Summer holidays"))

    assert response.status_code == 202
    assert response.json()["status"] == "SUCCEEDED"
    summary = client.get("/api/v1/forecast").json()["summary"]
    assert summary["first_breach"] == {
        "date": "2025-06-15",
        "closing": "-751625",
        "shortfall": "251625",
    }
    assert summary["lowest"] == {"date": "2025-07-03", "closing": "-2276082"}
    assert summary["days_below_floor"] == 50
    assert summary["end_balance"] == "-2097450"


def test_closure_is_stored_and_version_bumped(client: TestClient) -> None:
    post_event(client, closure(note="Summer holidays", variable_costs_stop=True))

    [event] = client.get("/api/v1/planned-events").json()
    assert event | {"id": None} == {
        "id": None,
        "kind": "CLOSURE",
        "date_from": "2025-06-01",
        "date_to": "2025-06-30",
        "revenue_factor": "0",
        "variable_costs_stop": True,
        "note": "Summer holidays",
    }
    assert client.get("/api/v1/company").json()["financial_version"] == 2


def test_run_can_be_polled(client: TestClient) -> None:
    run_id = post_event(client, closure()).json()["run_id"]
    run = client.get(f"/api/v1/runs/{run_id}").json()

    assert run["run_id"] == run_id
    assert (run["status"], run["mode"]) == ("SUCCEEDED", "FALLBACK")
    assert run["trigger"] == {
        "type": "CLOSURE_PLANNED",
        "label": "You plan to close 1 Jun – 30 Jun",
    }
    assert run["result"]["forecast_id"] == client.get("/api/v1/forecast").json()["id"]
    assert run["created_at"] == run["finished_at"]


def test_replay_returns_same_run_without_second_closure(client: TestClient) -> None:
    key = str(uuid4())
    first = post_event(client, closure(), key)
    second = post_event(client, closure(), key)

    assert second.status_code == 202
    assert second.json() == first.json()
    assert len(client.get("/api/v1/planned-events").json()) == 1
    assert client.get("/api/v1/company").json()["financial_version"] == 2


def test_stale_version_is_rejected(client: TestClient) -> None:
    post_event(client, closure())
    response = post_event(client, closure("2025-07-10", "2025-07-12", version=1))

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["current"] == {"financial_version": 2}


def test_missing_idempotency_key(client: TestClient) -> None:
    response = client.post("/api/v1/events", json=closure())
    assert response.status_code == 400
    assert response.json()["code"] == "IDEMPOTENCY_KEY_MISSING"


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (closure("2025-06-30", "2025-06-01"), "before date_from"),
        (closure("2025-05-01", "2025-05-10"), "cannot start before today"),
        (closure("2025-06-01", "2026-12-31"), "at most"),
    ],
)
def test_invalid_closure_dates(client: TestClient, body: dict, detail: str) -> None:
    response = post_event(client, body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert detail in response.json()["detail"]
    assert client.get("/api/v1/company").json()["financial_version"] == 1


def test_overlapping_closure_is_rejected(client: TestClient) -> None:
    post_event(client, closure())
    response = post_event(client, closure("2025-06-20", "2025-07-05", version=2))

    assert response.status_code == 422
    assert "overlaps" in response.json()["detail"]


def test_second_closure_after_first(client: TestClient) -> None:
    post_event(client, closure())
    response = post_event(client, closure("2025-07-20", "2025-07-21", version=2))

    assert response.status_code == 202
    assert len(client.get("/api/v1/planned-events").json()) == 2
    assert client.get("/api/v1/company").json()["financial_version"] == 3


@pytest.mark.parametrize(
    "body",
    [
        {"type": "SOMETHING_ELSE", "expected_financial_version": 1, "payload": {}},
        closure() | {"unexpected": True},
        {"type": "CLOSURE_PLANNED", "expected_financial_version": 1, "payload": {}},
        closure(version=0),
    ],
)
def test_malformed_events_are_rejected(client: TestClient, body: dict) -> None:
    response = post_event(client, body)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_unknown_run_is_not_found(client: TestClient) -> None:
    response = client.get(f"/api/v1/runs/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_reset_removes_closure(client: TestClient) -> None:
    post_event(client, closure())
    client.post("/api/v1/demo/reset")

    assert client.get("/api/v1/planned-events").json() == []
    assert client.get("/api/v1/forecast").json()["summary"]["first_breach"] is None
