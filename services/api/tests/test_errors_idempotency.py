from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import APIRouter, Request, status
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api.config import Settings, get_settings
from api.deps import RepositoryDep
from api.idempotency import (
    IdempotencyKeyDep,
    check_financial_version,
    request_hash,
    run_idempotent,
)
from api.main import create_app

settings = get_settings()
needs_sample = pytest.mark.skipif(
    not settings.statement_path.exists(),
    reason=f"sample CSV not found at {settings.statement_path}",
)
PROBLEM = "application/problem+json"


class ChangeIn(BaseModel):
    expected_financial_version: int
    note: str


class ChangeOut(BaseModel):
    note: str
    financial_version: int
    change_id: str


changes_router = APIRouter(prefix="/api/v1/test")


@changes_router.post("/changes", status_code=status.HTTP_202_ACCEPTED)
def make_change(
    body: ChangeIn, key: IdempotencyKeyDep, repository: RepositoryDep, request: Request
) -> ChangeOut:
    with repository.write() as state:

        def apply() -> ChangeOut:
            check_financial_version(state, body.expected_financial_version)
            state.financial_version += 1
            return ChangeOut(
                note=body.note, financial_version=state.financial_version, change_id=str(uuid4())
            )

        return run_idempotent(state, key, request_hash("POST", request.url.path, body), apply)


@pytest.fixture
def client():
    app = create_app(settings)
    app.include_router(changes_router)
    with TestClient(app) as test_client:
        yield test_client


def post(client: TestClient, body: dict, key: str | None):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post("/api/v1/test/changes", json=body, headers=headers)


def assert_problem(response, status_code: int, code: str) -> dict:
    assert response.status_code == status_code
    assert response.headers["content-type"] == PROBLEM
    problem = response.json()
    assert problem["status"] == status_code
    assert problem["code"] == code
    assert problem["type"] == f"urn:vero:problem:{code.lower()}"
    assert problem["title"]
    return problem


@needs_sample
def test_missing_idempotency_key(client: TestClient) -> None:
    response = post(client, {"expected_financial_version": 1, "note": "a"}, None)
    assert_problem(response, 400, "IDEMPOTENCY_KEY_MISSING")


@needs_sample
def test_idempotency_key_must_be_a_uuid(client: TestClient) -> None:
    response = post(client, {"expected_financial_version": 1, "note": "a"}, "not-a-uuid")
    assert_problem(response, 422, "VALIDATION_ERROR")


@needs_sample
def test_same_key_same_body_replays_without_side_effects(client: TestClient) -> None:
    key = str(uuid4())
    body = {"expected_financial_version": 1, "note": "closure"}
    first = post(client, body, key)
    second = post(client, body, key)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert first.json()["financial_version"] == 2
    assert client.get("/api/v1/company").json()["financial_version"] == 2


@needs_sample
def test_same_key_different_body_is_rejected(client: TestClient) -> None:
    key = str(uuid4())
    post(client, {"expected_financial_version": 1, "note": "closure"}, key)
    response = post(client, {"expected_financial_version": 1, "note": "something else"}, key)

    assert_problem(response, 409, "IDEMPOTENCY_KEY_REUSED")
    assert client.get("/api/v1/company").json()["financial_version"] == 2


@needs_sample
def test_stale_version_conflict_returns_current_version(client: TestClient) -> None:
    post(client, {"expected_financial_version": 1, "note": "first"}, str(uuid4()))
    response = post(client, {"expected_financial_version": 1, "note": "second"}, str(uuid4()))

    problem = assert_problem(response, 409, "VERSION_CONFLICT")
    assert problem["current"] == {"financial_version": 2}
    assert "expected financial_version 1" in problem["detail"]


@needs_sample
def test_failed_request_is_not_stored(client: TestClient) -> None:
    post(client, {"expected_financial_version": 1, "note": "first"}, str(uuid4()))
    key = str(uuid4())
    assert post(client, {"expected_financial_version": 1, "note": "x"}, key).status_code == 409
    retried = post(client, {"expected_financial_version": 2, "note": "x"}, key)

    assert retried.status_code == 202
    assert retried.json()["financial_version"] == 3


@needs_sample
def test_reset_forgets_idempotency_keys(client: TestClient) -> None:
    key = str(uuid4())
    body = {"expected_financial_version": 1, "note": "closure"}
    first = post(client, body, key).json()
    client.post("/api/v1/demo/reset")

    again = post(client, body, key).json()
    assert again["financial_version"] == 2
    assert again["change_id"] != first["change_id"]


@needs_sample
def test_validation_errors_list_fields(client: TestClient) -> None:
    response = post(client, {"note": 5}, str(uuid4()))
    problem = assert_problem(response, 422, "VALIDATION_ERROR")

    fields = {e["field"] for e in problem["errors"]}
    assert fields == {"body.expected_financial_version", "body.note"}


@needs_sample
def test_query_validation_error(client: TestClient) -> None:
    response = client.get("/api/v1/transactions", params={"category": "PIZZA"})
    problem = assert_problem(response, 422, "VALIDATION_ERROR")
    assert problem["errors"][0]["field"] == "query.category"


@needs_sample
def test_unknown_route_and_wrong_method(client: TestClient) -> None:
    assert_problem(client.get("/api/v1/nope"), 404, "NOT_FOUND")
    assert_problem(client.post("/api/v1/company"), 405, "METHOD_NOT_ALLOWED")


def with_settings(**changes) -> Settings:
    return Settings(**(settings.model_dump() | changes))


def test_demo_disabled_problem() -> None:
    with TestClient(create_app(with_settings(demo_mode=False))) as client:
        assert_problem(client.post("/api/v1/demo/reset"), 403, "DEMO_DISABLED")


def test_state_not_loaded_problem() -> None:
    with TestClient(create_app(with_settings(statement_path=Path("/nonexistent.csv")))) as client:
        assert_problem(client.get("/api/v1/forecast"), 503, "STATE_NOT_LOADED")
        assert_problem(client.post("/api/v1/demo/reset"), 503, "STATE_NOT_LOADED")


def test_request_hash_is_stable_and_order_independent() -> None:
    a = request_hash("post", "/x", ChangeIn(expected_financial_version=1, note="n"))
    b = request_hash("POST", "/x", ChangeIn(note="n", expected_financial_version=1))
    c = request_hash("POST", "/y", ChangeIn(expected_financial_version=1, note="n"))

    assert a == b
    assert a != c


def test_problem_is_documented_in_openapi() -> None:
    schemas = create_app(settings).openapi()["components"]["schemas"]
    assert {"type", "title", "status", "code"} <= set(schemas["Problem"]["required"])
