"""Agent reliability evaluation: run the same scenario N times through the real backend
and measure how often the model-driven agent (AGENT mode) succeeds.

Each round: POST /demo/reset -> POST /events (closure in June) -> poll GET /runs/{id}.
Per run it records mode (AGENT or FALLBACK), the fallback reason, latency, tool calls,
and the recommendations (consistency = same recommendations as the most common run). With --mlflow the summary
metrics, the per-run table and the scenario are logged as one MLflow run, so the
evaluation shows next to the agent traces of the same runs.

From a laptop (Windows CMD):
    oc port-forward deployment/api 8090:8080 -n autonomous-cfo
    python scripts/eval_agent.py --api http://localhost:8090/api/v1 -n 10

Inside the cluster (workbench terminal), with MLflow:
    python scripts/eval_agent.py --api http://api:8080/api/v1 -n 10 --mlflow http://mlflow:8080

Uses only the public API; DEMO_MODE must be on (POST /demo/reset). It resets the demo
company, so do not run it during a live demo.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from collections import Counter
from typing import Any

import httpx

DONE = {"SUCCEEDED", "FAILED"}
SCENARIO = {"type": "CLOSURE_PLANNED", "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"}}


def one_round(api: httpx.Client, scenario: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    r = api.post("/demo/reset")
    r.raise_for_status()
    version = api.get("/company").json()["financial_version"]
    body = dict(scenario, expected_financial_version=version)
    t0 = time.perf_counter()
    r = api.post("/events", json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    r.raise_for_status()
    run_id = r.json()["run_id"]

    run: dict[str, Any] = {}
    while time.perf_counter() - t0 < timeout_s:
        run = api.get(f"/runs/{run_id}").json()
        if run["status"] in DONE:
            break
        time.sleep(0.5)
    latency = time.perf_counter() - t0

    trace = run.get("trace") or []
    fallback = next((s for s in trace if s["kind"] == "FALLBACK"), None)
    result = run.get("result") or {}
    recs = []
    for rid in result.get("recommendation_ids") or []:
        rec = api.get(f"/recommendations/{rid}").json()
        s = rec.get("subject") or {}
        recs.append({"kind": rec.get("kind"), "lane": rec.get("lane"),
                     "ref": s.get("ref") or s.get("outflow_id"), "to_date": s.get("to_date"),
                     "amount": s.get("amount"), "available_from": s.get("available_from")})
    headline = result.get("headline") or {}
    return {
        "run_id": run_id,
        "status": run.get("status", "TIMEOUT"),
        "mode": run.get("mode"),
        "fallback_reason": ((fallback or {}).get("data") or {}).get("reason"),
        "latency_s": round(latency, 2),
        "tool_calls": sum(1 for s in trace if s["kind"] == "TOOL_CALL"),
        "discarded": sum(1 for s in trace if s["kind"] == "DISCARDED"),
        "agent_notes": sum(1 for s in trace if s["kind"] == "THINK" and s["source"] == "AGENT"),
        "recommendations": recs,
        "gap_closed": result.get("gap_closed"),
        "headline_source": headline.get("source"),
    }


def signature(row: dict[str, Any]) -> str:
    return json.dumps(sorted(json.dumps(r, sort_keys=True) for r in row["recommendations"]))


def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    n = len(rows)
    lat = sorted(r["latency_s"] for r in rows)
    agent = [r for r in rows if r["mode"] == "AGENT" and r["status"] == "SUCCEEDED"]
    common = Counter(signature(r) for r in rows).most_common(1)
    return {
        "runs": n,
        "agent_success_rate": len(agent) / n,
        "fallback_rate": sum(r["mode"] == "FALLBACK" for r in rows) / n,
        "consistency_rate": (common[0][1] / n) if common else 0.0,
        "model_headline_rate": sum(r["headline_source"] == "MODEL" for r in rows) / n,
        "latency_p50_s": statistics.median(lat),
        "latency_max_s": lat[-1],
        "avg_tool_calls": statistics.mean(r["tool_calls"] for r in rows),
        "avg_discarded_proposals": statistics.mean(r["discarded"] for r in rows),
    }


def log_mlflow(uri: str, experiment: str, summary: dict[str, float], rows: list[dict[str, Any]],
               scenario: dict[str, Any], model: str | None) -> None:
    import mlflow

    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=f"agent-eval-{time.strftime('%H%M%S')}") as run:
        mlflow.set_tags({"kind": "agent_eval", "scenario": scenario["type"], "model": model or "?"})
        mlflow.log_params({"runs": len(rows), "scenario": json.dumps(scenario)[:250], "model": model or "?"})
        mlflow.log_metrics({k: float(v) for k, v in summary.items()})
        mlflow.log_dict({"summary": summary, "runs": rows}, "eval_results.json")
        reasons = Counter(r["fallback_reason"] for r in rows if r["fallback_reason"])
        mlflow.log_dict(dict(reasons), "fallback_reasons.json")
        print(f"\nMLflow run: {uri.rstrip('/')}/#/experiments/{run.info.experiment_id}/runs/{run.info.run_id}")


def model_name(api: httpx.Client) -> str | None:
    try:
        r = api.get("/health/model")
        return r.json().get("model") if r.is_success else None
    except (httpx.HTTPError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", default="http://localhost:8090/api/v1", help="public API base URL (with /api/v1)")
    ap.add_argument("-n", type=int, default=10, help="number of runs")
    ap.add_argument("--timeout", type=float, default=120, help="seconds to wait per run")
    ap.add_argument("--mlflow", default="", help="MLflow tracking URI (optional)")
    ap.add_argument("--experiment", default="vero-agent")
    ap.add_argument("--out", default="", help="also write the results to this JSON file")
    args = ap.parse_args()

    rows = []
    with httpx.Client(base_url=args.api.rstrip("/"), timeout=30) as api:
        model = model_name(api)
        print(f"Evaluating {args.n} runs against {args.api} (model: {model or 'unknown'})")
        for i in range(args.n):
            try:
                row = one_round(api, SCENARIO, args.timeout)
            except httpx.HTTPError as e:
                print(f"  {i + 1:>2}: request failed: {e}")
                return 1
            rows.append(row)
            kinds = ",".join(r["kind"] for r in row["recommendations"]) or "-"
            print(f"  {i + 1:>2}: {row['mode'] or '?':8} {row['status']:9} {row['latency_s']:6.1f}s "
                  f"tools={row['tool_calls']} recs={kinds}"
                  + (f"  fallback={row['fallback_reason']}" if row["fallback_reason"] else ""))

    summary = summarize(rows)
    print("\nSummary")
    for k, v in summary.items():
        print(f"  {k:24} {v:.2f}" if isinstance(v, float) else f"  {k:24} {v}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "runs": rows}, f, indent=1)
    if args.mlflow:
        log_mlflow(args.mlflow, args.experiment, summary, rows, SCENARIO, model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
