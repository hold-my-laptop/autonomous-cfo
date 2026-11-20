from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.domain.models import PlannedEvent, PlannedEventKind
from api.main import create_app

settings = get_settings()
needs_sample = pytest.mark.skipif(
    not settings.statement_path.exists(),
    reason=f"sample CSV not found at {settings.statement_path}",
)


@pytest.fixture
def client():
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def add_june_closure(client: TestClient) -> None:
    with client.app.state.repository.write() as state:
        state.planned_events.append(
            PlannedEvent(uuid4(), PlannedEventKind.CLOSURE, date(2025, 6, 1), date(2025, 6, 30))
        )
        state.financial_version += 1


@needs_sample
def test_ready(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "state_loaded": True}


@needs_sample
def test_company(client: TestClient) -> None:
    assert client.get("/api/v1/company").json() == {
        "name": "Pizzeria Amalfi",
        "owner": "Vincenzo Amalfi",
        "currency": "EUR",
        "as_of_date": "2025-05-05",
        "balance": "40196",
        "statement_until": "2025-05-04",
        "financial_version": 1,
        "mandate_version": 1,
        "paused": False,
    }


@needs_sample
def test_transactions_are_history_only(client: TestClient) -> None:
    everything = client.get("/api/v1/transactions").json()
    assert everything[0]["booking_date"] == "2025-01-01"
    assert everything[-1]["booking_date"] == "2025-05-04"

    transfers = client.get("/api/v1/transactions", params={"category": "PARTNER_TRANSFER"}).json()
    assert [t["booking_date"] for t in transfers] == [
        "2025-01-15",
        "2025-02-15",
        "2025-03-15",
        "2025-04-15",
    ]
    assert transfers[-1]["amount"] == "-433334"
    assert transfers[-1]["original_amount"] == "-416667"
    assert transfers[-1]["fx_rate"] == "1.040"

    april = client.get("/api/v1/transactions", params={"from": "2025-04-01", "to": "2025-04-30"})
    assert {t["booking_date"][:7] for t in april.json()} == {"2025-04"}


@needs_sample
def test_transactions_reject_unknown_category(client: TestClient) -> None:
    assert client.get("/api/v1/transactions", params={"category": "PIZZA"}).status_code == 422


@needs_sample
def test_outflows(client: TestClient) -> None:
    outflows = client.get("/api/v1/outflows").json()
    by_ref = {o["ref"]: o for o in outflows}

    assert len(outflows) == 20
    assert by_ref["CHF-06"]["amount"] == "433334"
    assert by_ref["CHF-06"]["latest_allowed_date"] == "2025-07-30"
    assert by_ref["CHF-06"]["protected"] is False
    assert by_ref["PAY-06"]["protected"] is True
    assert by_ref["ITAX-06"] | {"id": None} == by_ref["ITAX-06"] | {
        "id": None,
        "ref": "ITAX-06",
        "description": "Income tax prepayment",
        "source": "OWNER",
        "protected": True,
        "amount": "115000",
    }


@needs_sample
def test_inflows_and_planned_events_start_empty(client: TestClient) -> None:
    assert client.get("/api/v1/inflows").json() == []
    assert client.get("/api/v1/planned-events").json() == []


@needs_sample
def test_revenue_model(client: TestClient) -> None:
    model = client.get("/api/v1/revenue-model").json()

    assert (model["window_from"], model["window_to"], model["weeks"]) == (
        "2025-03-10",
        "2025-05-04",
        8,
    )
    assert model["by_weekday"][0] == {
        "weekday": "MON",
        "revenue": "404472",
        "variable_costs": "-112471",
    }


@needs_sample
def test_mandate(client: TestClient) -> None:
    mandate = client.get("/api/v1/mandate").json()

    assert mandate["version"] == 1
    assert mandate["status"] == "ACTIVE"
    assert mandate["cash_floor"] == "-500000"
    assert mandate["act_alone_enabled"] is False
    assert mandate["protected_categories"] == ["PAYROLL", "SOCIAL_SECURITY", "TAX"]
    assert [m["version"] for m in client.get("/api/v1/mandate/versions").json()] == [1]


@needs_sample
def test_forecast_s0(client: TestClient) -> None:
    forecast = client.get("/api/v1/forecast").json()

    assert forecast["label"] == "CURRENT"
    assert forecast["start_balance"] == "40196"
    assert forecast["summary"] == {
        "first_breach": None,
        "lowest": {"date": "2025-06-01", "closing": "-39630"},
        "days_below_floor": 0,
        "end_balance": "239220",
        "headroom_at_lowest": "460370",
    }
    assert len(forecast["days"]) == 91
    assert forecast["weeks"] is None


@needs_sample
def test_forecast_s1_and_weekly(client: TestClient) -> None:
    add_june_closure(client)
    forecast = client.get("/api/v1/forecast", params={"weekly": True}).json()

    assert forecast["summary"]["first_breach"] == {
        "date": "2025-06-15",
        "closing": "-751625",
        "shortfall": "251625",
    }
    assert forecast["summary"]["lowest"] == {"date": "2025-07-03", "closing": "-2276082"}
    assert forecast["summary"]["days_below_floor"] == 50
    assert forecast["financial_version"] == 2
    assert forecast["drivers"][0]["kind"] == "NO_REVENUE"
    assert forecast["drivers"][0]["amount"] == "4590578"
    assert len(forecast["planned_events"]) == 1

    weeks = forecast["weeks"]
    assert len(weeks) == 13
    assert (weeks[0]["start_date"], weeks[-1]["end_date"]) == ("2025-05-05", "2025-08-03")
    assert weeks[-1]["closing"] == forecast["summary"]["end_balance"]
    assert min(int(w["lowest"]) for w in weeks) == -2276082


@needs_sample
def test_forecast_id_changes_with_version(client: TestClient) -> None:
    before = client.get("/api/v1/forecast").json()["id"]
    assert client.get("/api/v1/forecast").json()["id"] == before
    add_june_closure(client)
    assert client.get("/api/v1/forecast").json()["id"] != before


@needs_sample
def test_backtest(client: TestClient) -> None:
    add_june_closure(client)
    backtest = client.get("/api/v1/forecast/backtest").json()

    assert backtest["complete"] is True
    assert len(backtest["days"]) == 91
    assert backtest["actual"]["lowest"] == {"date": "2025-07-03", "closing": "-2209760"}
    assert backtest["forecast"]["lowest"] == {"date": "2025-07-03", "closing": "-2276082"}
    assert backtest["actual"]["first_breach"]["date"] == "2025-06-15"
    assert backtest["lowest_error_bp"] == 300
    assert backtest["forecast_id"] == client.get("/api/v1/forecast").json()["id"]


def test_endpoints_return_503_when_state_is_not_loaded() -> None:
    broken = Settings(**(settings.model_dump() | {"statement_path": Path("/nonexistent.csv")}))
    with TestClient(create_app(broken)) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        ready = client.get("/api/v1/health/ready")
        assert ready.status_code == 503
        assert ready.json() == {"status": "unavailable", "state_loaded": False}
        for path in ("/api/v1/company", "/api/v1/forecast", "/api/v1/outflows"):
            assert client.get(path).status_code == 503


@needs_sample
def test_money_is_documented_as_string() -> None:
    schema = create_app(settings).openapi()["components"]["schemas"]
    assert schema["Company"]["properties"]["balance"]["type"] == "string"


@needs_sample
@pytest.mark.parametrize(("retry_s", "ready_after_file"), [(0.0, True), (60.0, False)])
def test_state_loads_on_a_later_request_after_a_failed_start(
    tmp_path: Path, retry_s: float, ready_after_file: bool
) -> None:
    statement = tmp_path / "statement.csv"
    late = Settings(
        **(settings.model_dump() | {"statement_path": statement, "state_load_retry_s": retry_s})
    )
    with TestClient(create_app(late)) as client:
        assert client.get("/api/v1/health/ready").status_code == 503

        statement.write_bytes(settings.statement_path.read_bytes())

        ready = client.get("/api/v1/health/ready")
        assert (ready.status_code == 200) is ready_after_file
        if ready_after_file:
            assert client.get("/api/v1/company").json()["as_of_date"] == "2025-05-05"
