from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from api.agent import AgentUnavailableError, HttpAgentClient
from api.config import Settings, get_settings
from api.main import app, create_app
from api.schemas import ModelHealth

client = TestClient(app)


def test_live() -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_is_404() -> None:
    assert client.get("/api/v1/does-not-exist").status_code == 404


TOKEN = "test-internal-token"
LIVE = {"ok": True, "model": "granite-3.3-8b-instruct", "mode": "live", "latency_ms": 412}


class FakeAI:
    def __init__(self, health: dict[str, Any]) -> None:
        self.health = health
        self.checks = 0

    def start_run(self, payload: dict[str, Any]) -> None:
        raise AgentUnavailableError("not used")

    def explain(self, payload: dict[str, Any]) -> Any:
        raise AgentUnavailableError("not used")

    def model_health(self) -> ModelHealth:
        self.checks += 1
        return ModelHealth.model_validate(self.health)


def settings(**changes: Any) -> Settings:
    return Settings(**(get_settings().model_dump() | {"internal_token": TOKEN} | changes))


def model_health(app_settings: Settings, ai: FakeAI | None = None):
    with TestClient(create_app(app_settings, ai)) as test_client:
        return test_client.get("/api/v1/health/model")


def test_model_reachable() -> None:
    response = model_health(settings(), FakeAI(LIVE))

    assert response.status_code == 200
    assert response.json() == LIVE | {"error": None}


def test_model_unreachable_is_503() -> None:
    down = LIVE | {"ok": False, "latency_ms": 15000, "error": "timeout"}

    response = model_health(settings(), FakeAI(down))

    assert response.status_code == 503
    assert response.json() == down


def test_model_health_without_agent_mode() -> None:
    ai = FakeAI(LIVE)

    response = model_health(settings(internal_token=None), ai)

    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert "INTERNAL_TOKEN" in response.json()["error"]
    assert ai.checks == 0


def test_real_client_without_ai_service() -> None:
    response = model_health(settings(ai_url="http://127.0.0.1:9", model_health_timeout_s=1.0))

    assert response.status_code == 503
    body = response.json()
    assert (body["ok"], body["model"], body["mode"]) == (False, "unknown", None)
    assert body["error"] == "AI service unavailable: ConnectError"


def test_http_client_passes_the_ai_answer_through(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        calls.append((url, kwargs))
        return httpx.Response(200, json=LIVE)

    monkeypatch.setattr(httpx, "get", fake_get)

    result = HttpAgentClient(settings(ai_url="http://ai:8080/")).model_health()

    assert result.model_dump() == LIVE | {"error": None}
    [(url, kwargs)] = calls
    assert url == "http://ai:8080/health/model"
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert kwargs["timeout"] == 20.0


@pytest.mark.parametrize(
    ("status", "content"),
    [(200, b"not json"), (500, b'{"detail": "boom"}'), (200, b'{"ok": "maybe"}')],
)
def test_http_client_unusable_answer(
    monkeypatch: pytest.MonkeyPatch, status: int, content: bytes
) -> None:
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(status, content=content))

    result = HttpAgentClient(settings()).model_health()

    assert (result.ok, result.model) == (False, "unknown")
    assert result.error.startswith("AI service unavailable")
