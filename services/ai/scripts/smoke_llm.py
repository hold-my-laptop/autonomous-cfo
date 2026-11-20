"""Check that the model is reachable and can call tools. Run it inside the cluster
(workbench terminal or `oc rsh` into the ai pod) or through a port-forward:

    LLM_BASE_URL=http://vero-llm-predictor.autonomous-cfo.svc.cluster.local:8080/v1 python scripts/smoke_llm.py

Prints PASS/FAIL per check and the latency of each call."""

import json
import os
import sys
import time

from openai import OpenAI

base = os.environ.get("LLM_BASE_URL")
if not base:
    sys.exit("Set LLM_BASE_URL (including /v1).")
client = OpenAI(base_url=base, api_key=os.environ.get("LLM_API_KEY", "not-needed"), timeout=60, max_retries=0)

t = time.perf_counter()
model = os.environ.get("LLM_MODEL") or client.models.list().data[0].id
print(f"PASS models: {model} ({(time.perf_counter() - t) * 1000:.0f} ms)")

t = time.perf_counter()
r = client.chat.completions.create(model=model, temperature=0, max_tokens=60,
                                   messages=[{"role": "user", "content": "Say hello in five words."}])
print(f"PASS chat: {r.choices[0].message.content!r} ({(time.perf_counter() - t) * 1000:.0f} ms)")

tool = {"type": "function", "function": {
    "name": "get_forecast", "description": "Cash forecast for the next 91 days: lowest balance and first breach.",
    "parameters": {"type": "object", "properties": {}}}}
t = time.perf_counter()
r = client.chat.completions.create(model=model, temperature=0, max_tokens=200, tools=[tool], tool_choice="auto",
                                   messages=[{"role": "user", "content": "Will my cash go below zero? Check it."}])
calls = r.choices[0].message.tool_calls or []
ms = (time.perf_counter() - t) * 1000
if calls and calls[0].function.name == "get_forecast":
    print(f"PASS tool calling: {calls[0].function.name}({calls[0].function.arguments}) ({ms:.0f} ms)")
else:
    print(f"FAIL tool calling: no tool call. Content: {r.choices[0].message.content!r} ({ms:.0f} ms)")
    print("     Check vLLM args: --enable-auto-tool-choice --tool-call-parser hermes")
    sys.exit(1)

t = time.perf_counter()
for i in range(8):
    client.chat.completions.create(model=model, temperature=0, max_tokens=150, tools=[tool], tool_choice="auto",
                                   messages=[{"role": "user", "content": f"Check my cash forecast ({i})."}])
total = time.perf_counter() - t
print(f"{'PASS' if total < 60 else 'WARN'} 8 tool-calling requests in {total:.1f} s (budget 60 s per run)")
print(json.dumps({"model": model, "eight_calls_s": round(total, 1)}))
