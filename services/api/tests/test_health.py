from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_live() -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_is_404() -> None:
    assert client.get("/api/v1/does-not-exist").status_code == 404
