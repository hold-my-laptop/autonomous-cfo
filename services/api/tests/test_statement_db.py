import csv
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, delete, insert, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from api.config import Settings, get_settings
from api.main import create_app
from api.persistence import PostgresStateRepository
from api.state import build_demo_state
from api.statement_db import CSV_COLUMNS, statement_lines, statement_metadata

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://app:change-me@localhost:5433/app"
)


def _engine() -> Engine:
    return create_engine(Settings(database_url=TEST_DATABASE_URL).sqlalchemy_url)


def _database_reachable() -> bool:
    try:
        with _engine().connect() as connection:
            connection.execute(text("select 1"))
        return True
    except OperationalError:
        return False


def db_settings(**changes) -> Settings:
    values = get_settings().model_dump() | {
        "database_url": TEST_DATABASE_URL,
        "statement_source": "db",
        "reset_on_startup": True,
    }
    return Settings(**(values | changes))


def test_db_source_needs_database_url() -> None:
    with pytest.raises(ValidationError, match="STATEMENT_SOURCE=db needs DATABASE_URL"):
        Settings(statement_source="db", database_url=None)


def test_csv_is_the_default_source() -> None:
    assert Settings(database_url=None).statement_source == "csv"


needs_database = pytest.mark.skipif(
    not get_settings().statement_path.exists() or not _database_reachable(),
    reason=f"sample CSV not found or no database at {TEST_DATABASE_URL}",
)


@pytest.fixture
def engine() -> Iterator[Engine]:
    """The sample CSV loaded into the statement table, one row per CSV line."""
    engine = _engine()
    statement_metadata.drop_all(engine)
    statement_metadata.create_all(engine)
    column_names = {csv_name: name for name, csv_name in CSV_COLUMNS.items()}
    with get_settings().statement_path.open(encoding="utf-8-sig", newline="") as file:
        rows = [
            {"line_no": line} | {column_names[key]: value for key, value in row.items()}
            for line, row in enumerate(csv.DictReader(file, delimiter=";"), start=2)
        ]
    with engine.begin() as connection:
        connection.execute(insert(statement_lines), rows)
    yield engine
    statement_metadata.drop_all(engine)
    engine.dispose()


@needs_database
@pytest.mark.usefixtures("engine")
def test_db_statement_gives_the_same_state_as_the_csv() -> None:
    repository = PostgresStateRepository(db_settings())
    repository.start()
    with repository.read() as state:
        assert state == build_demo_state(get_settings())
    repository.close()


@needs_database
@pytest.mark.usefixtures("engine")
def test_demo_numbers_from_the_db_statement() -> None:
    with TestClient(create_app(db_settings())) as client:
        summary = client.get("/api/v1/forecast").json()["summary"]
        assert summary["lowest"] == {"date": "2025-06-01", "closing": "-39630"}
        assert summary["end_balance"] == "239220"

        client.post(
            "/api/v1/events",
            json={
                "type": "CLOSURE_PLANNED",
                "expected_financial_version": 1,
                "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"},
            },
            headers={"Idempotency-Key": str(uuid4())},
        )
        backtest = client.get("/api/v1/forecast/backtest").json()
        assert backtest["forecast"]["first_breach"]["closing"] == "-751625"
        assert backtest["actual"]["lowest"] == {"date": "2025-07-03", "closing": "-2209760"}
        assert backtest["lowest_error_bp"] == 300


@needs_database
def test_missing_table_is_an_error_not_the_csv() -> None:
    engine = _engine()
    statement_metadata.drop_all(engine)
    engine.dispose()
    with TestClient(create_app(db_settings())) as client:
        assert client.get("/api/v1/health/ready").status_code == 503
        response = client.get("/api/v1/forecast")
        assert (response.status_code, response.json()["code"]) == (503, "STATE_NOT_LOADED")


@needs_database
def test_empty_table_is_an_error(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(delete(statement_lines))
    with TestClient(create_app(db_settings())) as client:
        assert client.get("/api/v1/health/ready").status_code == 503
        reset = client.post("/api/v1/demo/reset")
        assert reset.status_code == 503
        assert "statement has no transactions" in reset.json()["detail"]


@needs_database
def test_unreachable_database_is_an_error_not_the_csv() -> None:
    settings = db_settings(database_url="postgresql://app:wrong@localhost:1/app")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health/ready").status_code == 503


@needs_database
def test_reset_reads_the_table_again(engine: Engine) -> None:
    with TestClient(create_app(db_settings())) as client:
        assert client.get("/api/v1/company").json()["statement_until"] == "2025-05-04"
        with engine.begin() as connection:
            connection.execute(
                delete(statement_lines).where(statement_lines.c.booking_date == "04.05.25")
            )
        assert client.post("/api/v1/demo/reset").status_code == 200
        assert client.get("/api/v1/company").json()["statement_until"] == "2025-05-03"
