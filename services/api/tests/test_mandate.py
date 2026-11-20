from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import create_app

settings = get_settings()
pytestmark = pytest.mark.skipif(not settings.statement_path.exists(), reason="sample CSV not found")

V2 = {
    "cash_floor": "-500000",
    "act_alone_enabled": True,
    "max_delay_days": 45,
    "max_amount_per_action": "500000",
    "max_amount_per_day": "500000",
    "protected_categories": ["PAYROLL", "SOCIAL_SECURITY", "TAX"],
    "credit_enabled": True,
    "max_credit_amount": "3000000",
    "note": "Vero may postpone transfers up to 5 000 € on its own",
}


@pytest.fixture
def client():
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def post(client: TestClient, path: str, body: dict | None = None, key: str | None = None):
    return client.post(
        f"/api/v1/mandate{path}", json=body, headers={"Idempotency-Key": key or str(uuid4())}
    )


def test_draft_does_not_change_active_rules(client: TestClient) -> None:
    response = post(client, "", V2)

    assert response.status_code == 201
    draft = response.json()
    assert (draft["version"], draft["status"], draft["activated_at"]) == (2, "DRAFT", None)
    assert draft["act_alone_enabled"] is True
    assert client.get("/api/v1/mandate").json()["version"] == 1
    versions = client.get("/api/v1/mandate/versions").json()
    assert [(v["version"], v["status"]) for v in versions] == [(2, "DRAFT"), (1, "ACTIVE")]


def test_activate_v2(client: TestClient) -> None:
    post(client, "", V2)
    response = post(client, "/2/activate", {"expected_active_version": 1})

    assert response.status_code == 200
    active = client.get("/api/v1/mandate").json()
    assert (active["version"], active["status"], active["act_alone_enabled"]) == (2, "ACTIVE", True)
    assert active["activated_at"] is not None
    versions = client.get("/api/v1/mandate/versions").json()
    assert [(v["version"], v["status"]) for v in versions] == [(2, "ACTIVE"), (1, "SUPERSEDED")]
    assert client.get("/api/v1/company").json()["mandate_version"] == 2


def test_activation_with_stale_version_conflicts(client: TestClient) -> None:
    post(client, "", V2)
    post(client, "", V2 | {"note": "another draft"})
    post(client, "/2/activate", {"expected_active_version": 1})
    response = post(client, "/3/activate", {"expected_active_version": 1})

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["current"] == {"mandate_version": 2}


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"protected_categories": ["PAYROLL", "SOCIAL_SECURITY"]}, "TAX must stay protected"),
        ({"protected_categories": []}, "must stay protected"),
        ({"max_amount_per_action": "600000"}, "cannot exceed"),
    ],
)
def test_invalid_rules_are_rejected(client: TestClient, change: dict, detail: str) -> None:
    response = post(client, "", V2 | change)

    assert response.status_code == 422
    assert response.json()["code"] == "MANDATE_INVALID"
    assert detail in response.json()["detail"]
    assert len(client.get("/api/v1/mandate/versions").json()) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"max_delay_days": 91},
        {"max_amount_per_day": "-1"},
        {"protected_categories": ["PIZZA"]},
        {"cash_floor": 12.5},
        {"unknown": True},
    ],
)
def test_malformed_rules_are_validation_errors(client: TestClient, change: dict) -> None:
    response = post(client, "", V2 | change)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_extra_protected_categories_are_allowed(client: TestClient) -> None:
    draft = post(
        client, "", V2 | {"protected_categories": ["PAYROLL", "SOCIAL_SECURITY", "TAX", "RENT"]}
    )
    assert draft.json()["protected_categories"] == ["PAYROLL", "RENT", "SOCIAL_SECURITY", "TAX"]


def test_cannot_activate_unknown_or_non_draft(client: TestClient) -> None:
    missing = post(client, "/9/activate", {"expected_active_version": 1})
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")

    active = post(client, "/1/activate", {"expected_active_version": 1})
    assert (active.status_code, active.json()["code"]) == (422, "MANDATE_INVALID")


def test_pause_and_resume(client: TestClient) -> None:
    paused = post(client, "/pause")
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert client.get("/api/v1/company").json()["paused"] is True

    post(client, "", V2)
    post(client, "/2/activate", {"expected_active_version": 1})
    assert client.get("/api/v1/mandate").json()["paused"] is True

    assert post(client, "/resume").json()["paused"] is False
    assert client.get("/api/v1/company").json()["paused"] is False


def test_create_is_idempotent(client: TestClient) -> None:
    key = str(uuid4())
    first = post(client, "", V2, key)
    second = post(client, "", V2, key)

    assert second.status_code == 201
    assert second.json() == first.json()
    assert len(client.get("/api/v1/mandate/versions").json()) == 2


def test_writes_need_idempotency_key(client: TestClient) -> None:
    response = client.post("/api/v1/mandate/pause")
    assert (response.status_code, response.json()["code"]) == (400, "IDEMPOTENCY_KEY_MISSING")


def test_new_cash_floor_changes_the_forecast(client: TestClient) -> None:
    client.post(
        "/api/v1/events",
        json={
            "type": "CLOSURE_PLANNED",
            "expected_financial_version": 1,
            "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"},
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    before = client.get("/api/v1/forecast").json()
    post(client, "", V2 | {"cash_floor": "-2500000"})
    post(client, "/2/activate", {"expected_active_version": 1})
    after = client.get("/api/v1/forecast").json()

    assert before["summary"]["days_below_floor"] == 50
    assert after["cash_floor"] == "-2500000"
    assert after["summary"]["first_breach"] is None
    assert after["mandate_version"] == 2
    assert after["id"] != before["id"]


def test_reset_restores_mandate_v1(client: TestClient) -> None:
    post(client, "", V2)
    post(client, "/2/activate", {"expected_active_version": 1})
    client.post("/api/v1/demo/reset")

    assert [m["version"] for m in client.get("/api/v1/mandate/versions").json()] == [1]


def test_amount_too_large_for_storage_is_a_validation_error(client: TestClient) -> None:
    response = post(client, "", V2 | {"cash_floor": "-99999999999999999999"})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["errors"][0]["field"] == "body.cash_floor"
