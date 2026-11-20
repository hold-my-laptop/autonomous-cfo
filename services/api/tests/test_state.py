from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.domain.models import Category, MandateStatus, PlannedEvent, PlannedEventKind
from api.main import create_app
from api.state import InMemoryStateRepository, StateNotLoadedError, build_demo_state

settings = get_settings()
needs_sample = pytest.mark.skipif(
    not settings.statement_path.exists(),
    reason=f"sample CSV not found at {settings.statement_path}",
)


def june_closure() -> PlannedEvent:
    return PlannedEvent(uuid4(), PlannedEventKind.CLOSURE, date(2025, 6, 1), date(2025, 6, 30))


@needs_sample
def test_build_demo_state() -> None:
    state = build_demo_state(settings)

    assert state.company.name == "Pizzeria Amalfi"
    assert state.as_of == date(2025, 5, 5)
    assert state.statement_until == date(2025, 5, 4)
    assert state.balance == 40196
    assert len(state.statement.transactions) == 1125
    assert len(state.outflows) == 20
    assert len(state.recurring) == 6
    assert state.financial_version == 1
    assert state.window_end == date(2025, 8, 3)


@needs_sample
def test_initial_mandate_is_conservative() -> None:
    mandate = build_demo_state(settings).active_mandate

    assert (mandate.version, mandate.status) == (1, MandateStatus.ACTIVE)
    assert mandate.cash_floor == -500_000
    assert not mandate.act_alone_enabled
    assert (mandate.max_amount_per_action, mandate.max_amount_per_day) == (500_000, 500_000)
    assert mandate.protected_categories == {
        Category.PAYROLL,
        Category.SOCIAL_SECURITY,
        Category.TAX,
    }
    assert (mandate.credit_enabled, mandate.max_credit_amount) == (True, 3_000_000)


@needs_sample
def test_state_forecast_and_backtest() -> None:
    state = build_demo_state(settings)
    assert state.forecast().summary.first_breach is None
    assert state.forecast().summary.lowest.closing == -39630

    state.planned_events.append(june_closure())
    assert state.forecast().summary.lowest.closing == -2276082
    assert state.backtest().actual.lowest.closing == -2209760


@needs_sample
def test_building_twice_gives_identical_state() -> None:
    assert build_demo_state(settings) == build_demo_state(settings)


def test_repository_must_be_loaded() -> None:
    repository = InMemoryStateRepository(settings)
    with pytest.raises(StateNotLoadedError), repository.read():
        pass


@needs_sample
def test_reset_discards_changes() -> None:
    repository = InMemoryStateRepository(settings)
    repository.reset()
    with repository.write() as state:
        state.planned_events.append(june_closure())
        state.financial_version = 7

    repository.reset()
    with repository.read() as state:
        assert state == build_demo_state(settings)
        assert state.forecast().summary.first_breach is None


@needs_sample
def test_reset_endpoint() -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.repository.write() as state:
            state.planned_events.append(june_closure())
            state.financial_version = 3

        first = client.post("/api/v1/demo/reset")
        second = client.post("/api/v1/demo/reset")

        assert first.status_code == 200
        assert first.json() == {"reset": True, "as_of_date": "2025-05-05", "financial_version": 1}
        assert second.json() == first.json()
        with app.state.repository.read() as state:
            assert state.planned_events == []
            assert state.forecast().summary.lowest.closing == -39630


def test_reset_is_forbidden_outside_demo_mode() -> None:
    with TestClient(create_app(replace_settings(demo_mode=False))) as client:
        assert client.post("/api/v1/demo/reset").status_code == 403


def test_missing_statement_does_not_stop_the_app() -> None:
    app = create_app(replace_settings(statement_path=Path("/nonexistent/statement.csv")))
    with TestClient(app) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert client.post("/api/v1/demo/reset").status_code == 503


def replace_settings(**changes) -> Settings:
    return Settings(**(settings.model_dump() | changes))
