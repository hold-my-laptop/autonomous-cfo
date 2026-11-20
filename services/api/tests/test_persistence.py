import os
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from api.config import Settings, get_settings
from api.main import build_repository, create_app
from api.persistence import PostgresStateRepository
from api.state import InMemoryStateRepository, build_demo_state

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://app:change-me@localhost:5433/app"
)


def _database_reachable() -> bool:
    url = Settings(database_url=TEST_DATABASE_URL).sqlalchemy_url
    try:
        with create_engine(url).connect() as connection:
            connection.execute(text("select 1"))
        return True
    except OperationalError:
        return False


pytestmark = [
    pytest.mark.skipif(not get_settings().statement_path.exists(), reason="sample CSV not found"),
    pytest.mark.skipif(not _database_reachable(), reason=f"no database at {TEST_DATABASE_URL}"),
]


def pg_settings(**changes) -> Settings:
    values = get_settings().model_dump() | {
        "database_url": TEST_DATABASE_URL,
        "reset_on_startup": False,
    }
    return Settings(**(values | changes))


def closure() -> dict:
    return {
        "type": "CLOSURE_PLANNED",
        "expected_financial_version": 1,
        "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30", "note": "Holidays"},
    }


def test_database_url_selects_postgres() -> None:
    assert isinstance(build_repository(pg_settings()), PostgresStateRepository)
    assert isinstance(build_repository(pg_settings(database_url=None)), InMemoryStateRepository)


def test_plain_postgresql_url_gets_psycopg_driver() -> None:
    assert Settings(database_url="postgresql://u:p@db:5432/app").sqlalchemy_url == (
        "postgresql+psycopg://u:p@db:5432/app"
    )
    assert Settings(database_url=None).sqlalchemy_url is None


def test_fresh_database_state_equals_built_state() -> None:
    repository = PostgresStateRepository(pg_settings())
    repository.start()
    repository.reset()
    with repository.read() as state:
        assert state == build_demo_state(get_settings())
    repository.close()


def test_changes_survive_a_restart() -> None:
    with TestClient(create_app(pg_settings(reset_on_startup=True))) as client:
        run_id = client.post(
            "/api/v1/events", json=closure(), headers={"Idempotency-Key": str(uuid4())}
        ).json()["run_id"]

    with TestClient(create_app(pg_settings(reset_on_startup=False))) as client:
        assert client.get("/api/v1/company").json()["financial_version"] == 2
        [event] = client.get("/api/v1/planned-events").json()
        assert (event["date_from"], event["note"]) == ("2025-06-01", "Holidays")
        assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "SUCCEEDED"
        summary = client.get("/api/v1/forecast").json()["summary"]
        assert summary["lowest"] == {"date": "2025-07-03", "closing": "-2276082"}


def test_idempotency_replay_survives_a_restart() -> None:
    key = str(uuid4())
    with TestClient(create_app(pg_settings(reset_on_startup=True))) as client:
        first = client.post("/api/v1/events", json=closure(), headers={"Idempotency-Key": key})

    with TestClient(create_app(pg_settings())) as client:
        again = client.post("/api/v1/events", json=closure(), headers={"Idempotency-Key": key})
        assert again.status_code == 202
        assert again.json() == first.json()
        assert len(client.get("/api/v1/planned-events").json()) == 1


def test_different_demo_date_reseeds() -> None:
    with TestClient(create_app(pg_settings(reset_on_startup=True))) as client:
        client.post("/api/v1/events", json=closure(), headers={"Idempotency-Key": str(uuid4())})

    with TestClient(create_app(pg_settings(demo_as_of_date=date(2025, 5, 12)))) as client:
        company = client.get("/api/v1/company").json()
        assert (company["as_of_date"], company["financial_version"]) == ("2025-05-12", 1)
        assert client.get("/api/v1/planned-events").json() == []


def test_failed_write_is_rolled_back() -> None:
    with TestClient(create_app(pg_settings(reset_on_startup=True))) as client:
        client.post("/api/v1/events", json=closure(), headers={"Idempotency-Key": str(uuid4())})
        stale = client.post(
            "/api/v1/events",
            json=closure() | {"payload": {"date_from": "2025-07-10", "date_to": "2025-07-11"}},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert stale.status_code == 409
        assert len(client.get("/api/v1/planned-events").json()) == 1
        assert client.get("/api/v1/company").json()["financial_version"] == 2


def test_unreachable_database_is_not_ready() -> None:
    settings = pg_settings(database_url="postgresql://app:wrong@localhost:1/app")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert client.get("/api/v1/health/ready").status_code == 503
        response = client.get("/api/v1/forecast")
        assert response.status_code == 503
        assert response.json()["code"] == "STATE_NOT_LOADED"
