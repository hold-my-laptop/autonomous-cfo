"""LiveLLM against a fake OpenAI-compatible server (httpx MockTransport)."""
import asyncio
import json

import httpx
import pytest

from ai.config import Settings
from ai.llm import LiveLLM


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"object": "list", "data": [{"id": "vero-llm", "object": "model"}]})
    body = json.loads(request.content)
    assert body["model"] == "vero-llm" and body["temperature"] == 0 and body["tool_choice"] == "auto"
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 0, "model": "vero-llm",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call1", "type": "function",
                            "function": {"name": "get_forecast", "arguments": "{}"}}]}}]})


def test_live_llm_parses_tool_calls():
    s = Settings(llm_base_url="http://vllm.test/v1")
    llm = LiveLLM(s, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    reply = asyncio.run(llm.chat([{"role": "user", "content": "hi"}],
                                 [{"type": "function", "function": {"name": "get_forecast", "parameters": {}}}]))
    assert reply.tool_calls[0].name == "get_forecast"


def test_live_llm_refuses_without_base_url():
    with pytest.raises(RuntimeError):
        LiveLLM(Settings(llm_base_url=""))


def test_thinking_switch_is_sent_only_when_set():
    seen = []

    def capture(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return handler(request)
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "vero-llm",
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": "{}"}}]})

    for flag in (None, False):
        s = Settings(llm_base_url="http://vllm.test/v1", llm_enable_thinking=flag)
        llm = LiveLLM(s, http_client=httpx.AsyncClient(transport=httpx.MockTransport(capture)))
        asyncio.run(llm.chat([{"role": "user", "content": "hi"}]))
    assert "chat_template_kwargs" not in seen[0]
    assert seen[1]["chat_template_kwargs"] == {"enable_thinking": False}
