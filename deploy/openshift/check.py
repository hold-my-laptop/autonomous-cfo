import json
import sys
import time
import urllib.request
import uuid

API = "http://127.0.0.1:8080/api/v1"
AI = "http://ai:8080"
CLOSURE = {
    "type": "CLOSURE_PLANNED",
    "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30", "variable_costs_stop": True, "note": "summer closure"},
}
failed = []


def call(url, method="GET", body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'}  {name}  {detail}", flush=True)
    if not ok:
        failed.append(name)


def step(name, fn):
    try:
        fn()
    except Exception as e:
        check(name, False, f"{type(e).__name__}: {e}")


def health():
    check("api live", call(f"{API}/health/live").get("status") == "ok")
    ready = call(f"{API}/health/ready")
    check("api ready (database + statement loaded)", ready.get("state_loaded") is True, json.dumps(ready))
    check("ai service", bool(call(f"{AI}/health/live")))
    model = call(f"{API}/health/model")
    check("model reachable", model.get("ok") is True, f"{model.get('model')} {model.get('latency_ms')} ms")


def reset():
    r = call(f"{API}/demo/reset", "POST", {})
    check("demo reset", r.get("reset") is True, f"as_of_date={r.get('as_of_date')}")


def agent_run():
    version = call(f"{API}/company")["financial_version"]
    accepted = call(f"{API}/events", "POST", {**CLOSURE, "expected_financial_version": version})
    start = time.time()
    run = {}
    while time.time() - start < 90:
        run = call(f"{API}/runs/{accepted['run_id']}")
        if run.get("status") in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(1)
    seconds = time.time() - start
    tool_calls = [s["kind"] for s in run.get("trace", [])].count("TOOL_CALL")
    recs = (run.get("result") or {}).get("recommendation_ids", [])
    check("agent run finished", run.get("status") == "SUCCEEDED", f"status={run.get('status')}")
    check("agent used the model", run.get("mode") == "AGENT", f"mode={run.get('mode')}")
    check("agent within 60 s", seconds < 60, f"{seconds:.1f} s")
    check("agent within 8 tool calls", tool_calls <= 8, f"{tool_calls} calls")
    check("agent made recommendations", len(recs) > 0, f"{len(recs)} recommendations")


step("health", health)
step("demo reset", reset)
step("agent run", agent_run)
step("demo reset", reset)

print()
print("ALL CHECKS PASSED" if not failed else f"{len(failed)} CHECK(S) FAILED: {', '.join(failed)}")
sys.exit(1 if failed else 0)
