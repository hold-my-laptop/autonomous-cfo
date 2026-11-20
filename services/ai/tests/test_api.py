import time

from fastapi.testclient import TestClient

from ai.config import Settings
from ai.llm import StubLLM
from ai.main import create_app

from conftest import FakeBackend


def make(token=None):
    s = Settings(llm_mode="stub", internal_token=token)
    b = FakeBackend()
    return TestClient(create_app(s, llm=StubLLM(), backend=b)), b


def test_health():
    c, _ = make()
    with c:
        assert c.get("/health/live").json() == {"status": "ok"}
        assert c.get("/health").status_code == 200
        h = c.get("/health/model").json()
        assert h["ok"] and h["mode"] == "stub"


def test_start_run_is_async_and_completes():
    c, b = make()
    with c:
        r = c.post("/v1/agent/runs", json={"run_id": "r9", "event": {"type": "CLOSURE_PLANNED", "label": "x"}})
        assert r.status_code == 202 and r.json() == {"accepted": True}
        for _ in range(50):
            if b.completed:
                break
            time.sleep(0.05)
        assert b.completed and len(b.completed.proposals) == 2


def test_internal_token_required():
    c, _ = make(token="s3cret")
    with c:
        body = {"run_id": "r1", "event": {"type": "X"}}
        assert c.post("/v1/agent/runs", json=body).status_code == 401
        ok = c.post("/v1/agent/runs", json=body, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 202
