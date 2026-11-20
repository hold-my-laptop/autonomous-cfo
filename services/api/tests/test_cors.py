from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.main import create_app


def client_for(origins: list[str]) -> TestClient:
    settings = Settings(**(get_settings().model_dump() | {"cors_origins": origins}))
    return TestClient(create_app(settings))


def preflight(client: TestClient, origin: str, headers: str = "content-type,idempotency-key"):
    return client.options(
        "/api/v1/events",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": headers,
        },
    )


def test_allowed_origin_preflight() -> None:
    with client_for(["http://localhost:5173"]) as client:
        response = preflight(client, "http://localhost:5173")

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        allowed = response.headers["access-control-allow-headers"].lower()
        assert "idempotency-key" in allowed
        assert "access-control-allow-credentials" not in response.headers


def test_unknown_origin_is_refused() -> None:
    with client_for(["http://localhost:5173"]) as client:
        response = preflight(client, "https://evil.example")

        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers


def test_simple_request_gets_cors_header() -> None:
    with client_for(["http://localhost:5173"]) as client:
        response = client.get("/api/v1/health/live", headers={"Origin": "http://localhost:5173"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_origins_come_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", '["https://vero.apps.example.com","http://localhost:5173"]')
    assert Settings().cors_origins == ["https://vero.apps.example.com", "http://localhost:5173"]
