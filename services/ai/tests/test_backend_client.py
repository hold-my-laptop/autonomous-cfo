import asyncio
import json

import httpx

from ai.backend_client import HttpBackend
from ai.config import Settings


def test_http_backend_sends_token_and_maps_errors():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/tools"):
            return httpx.Response(200, json=[{"name": "get_forecast", "description": "d",
                                              "input_schema": {"type": "object"}}])
        if req.url.path.endswith("/tools/get_forecast"):
            body = json.loads(req.content)
            assert body == {"run_id": "r1", "arguments": {}, "call_id": "c1"}
            return httpx.Response(200, json={"ok": True, "result": {"x": 1}, "financial_version": 2,
                                             "tool_calls_used": 1})
        return httpx.Response(409, json={"code": "STALE_RUN", "title": "Stale"})

    s = Settings(api_internal_url="http://api:8080/internal/v1/", internal_token="t0k")
    b = HttpBackend(s, transport=httpx.MockTransport(handler))

    async def go():
        tools = await b.list_tools()
        ok = await b.call_tool("get_forecast", "r1", {}, "c1")
        bad = await b.call_tool("simulate_moves", "r1", {}, "c2")
        await b.aclose()
        return tools, ok, bad

    tools, ok, bad = asyncio.run(go())
    assert tools[0].name == "get_forecast" and ok.ok and ok.tool_calls_used == 1
    assert not bad.ok and bad.error.code == "STALE_RUN"
    assert all(r.headers["authorization"] == "Bearer t0k" for r in seen)
    assert str(seen[0].url) == "http://api:8080/internal/v1/tools"
