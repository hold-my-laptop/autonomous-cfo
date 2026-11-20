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
}


@pytest.fixture
def client():
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def post(client: TestClient, path: str, body: dict | None = None, key: str | None = None):
    return client.post(
        f"/api/v1{path}", json=body, headers={"Idempotency-Key": key or str(uuid4())}
    )


def activate_v2(client: TestClient) -> None:
    post(client, "/mandate", V2)
    post(client, "/mandate/2/activate", {"expected_active_version": 1})


def close_june(client: TestClient) -> dict:
    version = client.get("/api/v1/company").json()["financial_version"]
    run_id = post(
        client,
        "/events",
        {
            "type": "CLOSURE_PLANNED",
            "expected_financial_version": version,
            "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"},
        },
    ).json()["run_id"]
    return client.get(f"/api/v1/runs/{run_id}").json()


def lanes(client: TestClient) -> dict:
    return client.get("/api/v1/overview").json()["lanes"]


def test_demo_path_with_mandate_v2(client: TestClient) -> None:
    activate_v2(client)
    run = close_june(client)

    assert (run["status"], run["mode"]) == ("SUCCEEDED", "FALLBACK")
    assert run["result"]["counts"] == {
        "done_within_mandate": 1,
        "needs_approval": 1,
        "not_allowed": 0,
    }
    assert run["result"]["gap_closed"] is False
    kinds = [step["kind"] for step in run["trace"]]
    assert kinds[0] == "TOOL_CALL" and kinds[-1] == "SUMMARY"
    assert "EXECUTE" in kinds and "POLICY" in kinds
    assert all(step["source"] == "BACKEND" for step in run["trace"])

    overview = lanes(client)
    [done] = overview["DONE_WITHIN_MANDATE"]
    assert done["subject"]["ref"] == "CHF-06"
    assert (done["status"], done["subject"]["to_date"]) == ("EXECUTED", "2025-07-30")
    assert done["executed"]["executed_by"] == "VERO"
    assert done["executed"]["confirmation_ref"].startswith("SIM-")
    [credit] = overview["NEEDS_APPROVAL"]
    assert credit["kind"] == "CREDIT_LINE"
    assert credit["subject"]["amount"] == "2000000"
    assert credit["subject"]["available_from"] == "2025-06-27"
    assert credit["subject"]["illustrative"] is True
    assert credit["status"] == "PENDING"
    assert {f["kind"] for f in overview["ANALYSED"]} >= {"DRIVER", "RECURRING", "FX_EXPOSURE"}

    s2 = client.get("/api/v1/forecast").json()["summary"]
    assert s2["first_breach"]["date"] == "2025-06-27"
    assert s2["days_below_floor"] == 38
    chf = next(o for o in client.get("/api/v1/outflows").json() if o["ref"] == "CHF-06")
    assert (chf["scheduled_date"], chf["version"]) == ("2025-07-30", 2)


def test_approving_the_credit_line_gives_s3(client: TestClient) -> None:
    activate_v2(client)
    close_june(client)
    credit = lanes(client)["NEEDS_APPROVAL"][0]
    response = post(client, f"/recommendations/{credit['id']}/approve", {"note": "OK"})

    assert response.status_code == 200
    result = response.json()
    assert result["recommendation"]["status"] == "APPROVED_EXECUTED"
    assert result["recommendation"]["executed"]["executed_by"] == "OWNER"
    assert result["forecast_summary"]["first_breach"] is None
    assert result["forecast_summary"]["headroom_at_lowest"] == "391889"
    s3 = client.get("/api/v1/forecast").json()
    assert s3["summary"]["days_below_floor"] == 0
    assert s3["credit_lines"][0]["amount"] == "2000000"
    assert client.get("/api/v1/overview").json()["pending_count"] == 0

    receipt = client.get(f"/api/v1/receipts/{result['receipt_id']}").json()
    assert (receipt["kind"], receipt["decision"]) == ("DECISION", "APPROVED")
    assert receipt["before"]["days_below_floor"] == 38
    assert receipt["after"]["days_below_floor"] == 0
    assert receipt["execution"] == "SIMULATED"


def test_rejecting_keeps_s2(client: TestClient) -> None:
    activate_v2(client)
    close_june(client)
    credit = lanes(client)["NEEDS_APPROVAL"][0]
    result = post(client, f"/recommendations/{credit['id']}/reject").json()

    assert result["recommendation"]["status"] == "REJECTED"
    assert result["forecast_summary"]["days_below_floor"] == 38
    assert client.get("/api/v1/forecast").json()["credit_lines"] == []
    receipt = client.get(f"/api/v1/receipts/{result['receipt_id']}").json()
    assert receipt["decision"] == "REJECTED"


def test_mandate_v1_needs_approval_for_both(client: TestClient) -> None:
    run = close_june(client)

    assert run["result"]["counts"]["needs_approval"] == 2
    assert run["result"]["counts"]["done_within_mandate"] == 0
    pending = lanes(client)["NEEDS_APPROVAL"]
    assert [r["kind"] for r in pending] == ["RESCHEDULE_PAYMENT", "CREDIT_LINE"]
    assert pending[1]["subject"]["available_from"] == "2025-06-15"


def test_deciding_twice_is_not_pending(client: TestClient) -> None:
    activate_v2(client)
    close_june(client)
    credit_id = lanes(client)["NEEDS_APPROVAL"][0]["id"]
    post(client, f"/recommendations/{credit_id}/reject")
    response = post(client, f"/recommendations/{credit_id}/approve")

    assert (response.status_code, response.json()["code"]) == (409, "NOT_PENDING")


def test_stale_recommendation_is_marked_and_refused(client: TestClient) -> None:
    close_june(client)
    move, credit = lanes(client)["NEEDS_APPROVAL"]
    post(client, f"/recommendations/{move['id']}/approve")
    response = post(client, f"/recommendations/{credit['id']}/approve")

    assert (response.status_code, response.json()["code"]) == (409, "STALE_RECOMMENDATION")
    stale = client.get(f"/api/v1/recommendations/{credit['id']}").json()
    assert stale["status"] == "STALE"


def test_approve_is_idempotent(client: TestClient) -> None:
    activate_v2(client)
    close_june(client)
    credit_id = lanes(client)["NEEDS_APPROVAL"][0]["id"]
    key = str(uuid4())
    first = post(client, f"/recommendations/{credit_id}/approve", None, key)
    second = post(client, f"/recommendations/{credit_id}/approve", None, key)

    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(client.get("/api/v1/forecast").json()["credit_lines"]) == 1


def test_new_plan_supersedes_pending(client: TestClient) -> None:
    activate_v2(client)
    close_june(client)
    old_credit = lanes(client)["NEEDS_APPROVAL"][0]["id"]
    version = client.get("/api/v1/company").json()["financial_version"]
    post(
        client,
        "/events",
        {
            "type": "CLOSURE_PLANNED",
            "expected_financial_version": version,
            "payload": {"date_from": "2025-07-20", "date_to": "2025-07-21"},
        },
    )

    assert client.get(f"/api/v1/recommendations/{old_credit}").json()["status"] == "SUPERSEDED"
    assert old_credit not in [r["id"] for r in lanes(client)["NEEDS_APPROVAL"]]


def test_payroll_reschedule_request_is_not_allowed(client: TestClient) -> None:
    payroll = next(o for o in client.get("/api/v1/outflows").json() if o["ref"] == "PAY-06")
    response = post(
        client, f"/outflows/{payroll['id']}/reschedule-requests", {"to_date": "2025-07-15"}
    )

    assert response.status_code == 202
    run = client.get(f"/api/v1/runs/{response.json()['run_id']}").json()
    assert run["trigger"]["type"] == "RESCHEDULE_REQUESTED"
    [blocked] = lanes(client)["NOT_ALLOWED"]
    assert (blocked["status"], blocked["subject"]["ref"]) == ("BLOCKED", "PAY-06")
    assert {c["rule"] for c in blocked["checks"] if c["result"] == "FAIL"} >= {"R4", "VALIDITY"}
    receipt = client.get(f"/api/v1/receipts/{blocked['receipt_ids'][0]}").json()
    assert (receipt["decision"], receipt["lane"]) == ("REQUESTED", "NOT_ALLOWED")
    assert client.get("/api/v1/company").json()["financial_version"] == 1


def test_owner_may_postpone_a_movable_payment(client: TestClient) -> None:
    transfer = next(o for o in client.get("/api/v1/outflows").json() if o["ref"] == "CHF-07")
    post(client, f"/outflows/{transfer['id']}/reschedule-requests", {"to_date": "2025-08-20"})

    moved = next(o for o in client.get("/api/v1/outflows").json() if o["ref"] == "CHF-07")
    assert moved["scheduled_date"] == "2025-08-20"
    [done] = [r for r in lanes(client)["DONE_WITHIN_MANDATE"] if r["subject"]["ref"] == "CHF-07"]
    assert done["status"] == "APPROVED_EXECUTED"


def test_unknown_ids_are_not_found(client: TestClient) -> None:
    assert client.get(f"/api/v1/recommendations/{uuid4()}").status_code == 404
    assert client.get(f"/api/v1/receipts/{uuid4()}").status_code == 404
    assert post(client, f"/recommendations/{uuid4()}/approve").status_code == 404
    assert (
        post(client, f"/outflows/{uuid4()}/reschedule-requests", {"to_date": "2025-07-01"})
    ).status_code == 404


def test_overview_before_any_event(client: TestClient) -> None:
    overview = client.get("/api/v1/overview").json()

    assert overview["forecast_summary"]["first_breach"] is None
    assert overview["pending_count"] == 0
    assert overview["latest_run"] is None
    assert "stays above" in overview["headline"]["text"]
    assert overview["lanes"]["DONE_WITHIN_MANDATE"] == []


def test_runs_list_newest_first(client: TestClient) -> None:
    close_june(client)
    payroll = next(o for o in client.get("/api/v1/outflows").json() if o["ref"] == "PAY-06")
    post(client, f"/outflows/{payroll['id']}/reschedule-requests", {"to_date": "2025-07-15"})

    runs = client.get("/api/v1/runs").json()
    assert [r["trigger"]["type"] for r in runs] == ["RESCHEDULE_REQUESTED", "CLOSURE_PLANNED"]
