"""P18: the demo reset brings both phases back to the same state, whatever happened before."""

import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_investigations import AUTH, InvestigatingAI, make_client, start
from test_proof_actions import RENT_PDF, vpi_above_threshold

from api.config import Settings, get_settings
from api.main import create_app

base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")
S0 = {
    "first_breach": None,
    "lowest": {"date": "2025-06-01", "closing": "-39630"},
    "days_below_floor": 0,
    "end_balance": "239220",
}
DIGEST = {"changes": 3, "handled_quietly": 3, "needs_owner": 0, "routine_checks": 3}
RESET_LIMIT_S = 5.0


def post(client: TestClient, path: str, body: dict | None = None) -> dict:
    response = client.post(f"/api/v1{path}", json=body, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code < 300, response.text
    return response.json()


def reset(client: TestClient) -> float:
    """Reset the demo and return how long it took, in seconds."""
    started = time.perf_counter()
    post(client, "/demo/reset")
    return time.perf_counter() - started


def snapshot(client: TestClient) -> dict[str, object]:
    """Every public read without a path parameter, except health (it asks the model)."""
    paths = [
        path
        for path, operations in client.app.openapi()["paths"].items()
        if "get" in operations and "{" not in path and "/health" not in path
    ]
    result = {}
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        result[path] = response.json()
    return result


def summary(client: TestClient) -> dict:
    data = client.get("/api/v1/forecast").json()["summary"]
    return {k: data[k] for k in S0}


def upload_rent(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("Mietanpassung.pdf", RENT_PDF, "application/pdf")},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


def a_busy_demo(client: TestClient) -> None:
    """Both phases used: the hero decided, a quiet change acknowledged, a new mandate, the
    closure story run and its credit line approved."""
    accepted = upload_rent(client)
    [finding_id] = client.get(f"/api/v1/runs/{accepted['run_id']}").json()["finding_ids"]
    keep, letter = client.get(f"/api/v1/findings/{finding_id}").json()["recommendation_ids"]
    post(client, f"/recommendations/{keep}/approve")
    post(client, f"/recommendations/{letter}/reject")
    quiet = client.get("/api/v1/digest").json()["changes"][0]["finding_id"]
    post(client, f"/findings/{quiet}/acknowledge")
    mandate = client.get("/api/v1/mandate").json()
    rules = {
        k: v
        for k, v in mandate.items()
        if k not in ("version", "status", "paused", "created_at", "activated_at")
    }
    draft = post(client, "/mandate", rules | {"act_alone_enabled": True})
    post(
        client,
        f"/mandate/{draft['version']}/activate",
        {"expected_active_version": mandate["version"]},
    )
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
    pending = [r for r in client.get("/api/v1/recommendations").json() if r["status"] == "PENDING"]
    assert [r["kind"] for r in pending] == ["CREDIT_LINE"]
    post(client, f"/recommendations/{pending[0]['id']}/approve")
    assert summary(client) != S0


@pytest.fixture
def client():
    with TestClient(create_app(base)) as test_client:
        reset(test_client)
        yield test_client


def test_two_resets_give_the_same_demo(client: TestClient) -> None:
    first = snapshot(client)
    a_busy_demo(client)
    busy = snapshot(client)
    reset(client)
    second = snapshot(client)
    reset(client)
    third = snapshot(client)

    assert busy != first
    assert second == first
    assert third == first
    assert summary(client) == S0
    assert first["/api/v1/digest"]["counts"] == DIGEST
    assert (first["/api/v1/runs"], first["/api/v1/recommendations"]) == ([], [])
    assert [f["status"] for f in first["/api/v1/findings"]] == ["CLASSIFIED"] * 3


def test_the_reset_forgets_the_upload_and_its_file(client: TestClient) -> None:
    accepted = upload_rent(client)
    document = accepted["document_id"]
    assert client.get(f"/api/v1/documents/{document}/file").status_code == 200
    reset(client)

    assert client.get(f"/api/v1/documents/{document}").status_code == 404
    assert client.get(f"/api/v1/documents/{document}/file").status_code == 404
    assert client.get(f"/api/v1/runs/{accepted['run_id']}").status_code == 404
    again = upload_rent(client)
    assert again["status"] == "SUCCEEDED"
    [finding_id] = client.get(f"/api/v1/runs/{again['run_id']}").json()["finding_ids"]
    finding = client.get(f"/api/v1/findings/{finding_id}").json()
    assert (finding["classification"], finding["investigation"]["disputed"]) == (
        "CONFLICT",
        "30400",
    )


def test_the_reset_restores_an_executed_amount_change(tmp_path: Path) -> None:
    with TestClient(create_app(vpi_above_threshold(tmp_path))) as client:
        reset(client)
        first = snapshot(client)
        accepted = upload_rent(client)
        [finding_id] = client.get(f"/api/v1/runs/{accepted['run_id']}").json()["finding_ids"]
        for rec_id in client.get(f"/api/v1/findings/{finding_id}").json()["recommendation_ids"]:
            post(client, f"/recommendations/{rec_id}/approve")
        changed = summary(client)
        reset(client)

        assert changed != S0
        assert summary(client) == S0
        assert snapshot(client) == first


def test_a_reset_during_an_investigation_ends_it_quietly() -> None:
    ai = InvestigatingAI()
    with make_client(ai) as client:
        reset(client)
        first = snapshot(client)
        run_id = start(client)
        assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "RUNNING"
        reset(client)
        late_tool = client.post(
            "/internal/v1/tools/search_inbox",
            json={"run_id": run_id, "arguments": {"query": "rent"}, "call_id": "c1"},
            headers=AUTH,
        )
        late_complete = client.post(
            f"/internal/v1/runs/{run_id}/fail", json={"reason": "MODEL_ERROR"}, headers=AUTH
        )
        client.app.state.proof.fall_back(run_id, "DEADLINE_PASSED")

        assert [p["run_id"] for p in ai.started] == [run_id]
        assert late_tool.status_code == 200
        assert (late_tool.json()["ok"], late_tool.json()["error"]["code"]) == (False, "RUN_CLOSED")
        assert late_complete.status_code == 409
        assert late_complete.json()["code"] == "RUN_CLOSED"
        assert snapshot(client) == first
        assert upload_rent(client)["status"] == "RUNNING"


def test_the_reset_is_fast(client: TestClient) -> None:
    a_busy_demo(client)
    durations = [reset(client) for _ in range(3)]

    assert max(durations) < RESET_LIMIT_S, durations


def test_without_demo_mode_there_is_no_reset() -> None:
    settings = Settings(**(base.model_dump() | {"demo_mode": False}))
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/demo/reset", headers={"Idempotency-Key": str(uuid4())})

    assert (response.status_code, response.json()["code"]) == (403, "DEMO_DISABLED")
