# services/ai — agent runtime

Thin AI service from `docs/contracts/DATA_CONTRACTS.md` section 3. The backend owns all data
and logic; this service only runs the tool-calling loop against our model (Qwen3-8B served
by vLLM on OpenShift AI) and writes plain-language texts.

```
backend ──POST /v1/agent/runs──▶ ai ──GET /tools, POST /tools/{name}──▶ backend (/internal/v1)
                                  │ ⇅ vLLM (OpenAI-compatible API, inside the cluster)
                                  └──POST /runs/{id}/complete | /fail──▶ backend (FALLBACK on /fail)
backend ──POST /v1/explain──▶ ai (texts checked against facts, else templates)
```

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health/live`, `/health` | Liveness, no model call |
| GET | `/health/model` | Deep check: `{ok, model, mode, latency_ms, error}` |
| POST | `/v1/agent/runs` | `AgentRunStart` → `202 {accepted: true}`, runs in the background |
| POST | `/v1/explain` | `ExplainRequest` → `ExplainResult` (`MODEL` or `TEMPLATE`) |

`/v1/*` needs `Authorization: Bearer $INTERNAL_TOKEN` when `INTERNAL_TOKEN` is set.

## How a run works

1. Fetch tool specs from the backend and offer them to the model, plus a local `submit_proposals` tool.
2. Loop: model picks a tool → backend runs it (and writes the trace) → result goes back to the model.
   Limits from `AgentRunStart.limits` (default 8 tool calls, 60 s).
3. `submit_proposals` is checked against this run's tool results: moves must be planner candidates,
   a credit line must equal the last `size_credit_line` result. Up to 2 corrections are sent back to the model.
4. Success → `POST /runs/{id}/complete` (AGENT mode). Anything else → `POST /runs/{id}/fail`
   (`MODEL_TIMEOUT`, `MODEL_ERROR`, `INVALID_TOOL_CALLS`, `NO_FINAL_ANSWER`, `TOOL_LIMIT_REACHED`) and the backend runs FALLBACK.
   `STALE_RUN` / `RUN_CLOSED` from a tool stop the run quietly.

The model never computes money: amounts are copied from tool results (integer cents).

## Configuration (env vars)

See `.env.example`. `LLM_MODE=live` refuses to start without `LLM_BASE_URL`, so it can never fall back
to a public API. `LLM_MODE=stub` is a scripted agent for local development only — never for the demo.

## Run locally

```bash
cd services/ai
pip install -r requirements-dev.txt
pytest -q                                  # fake model + fake backend
LLM_MODE=stub uvicorn ai.main:app --app-dir src --port 8080
```

Check the real model (inside the cluster or through a port-forward):

```bash
LLM_BASE_URL=http://vero-llm-predictor.autonomous-cfo.svc.cluster.local:8080/v1 python scripts/smoke_llm.py
```

## Deploy on OpenShift

```bash
oc project autonomous-cfo
oc create secret generic internal-token --from-literal=INTERNAL_TOKEN=$(openssl rand -hex 24)  # once; api uses the same secret
oc apply -f deploy/openshift/ai/
oc start-build ai --follow
```

The backend must call `http://ai:8080` (set `AI_URL` in the api deployment) and send the same `INTERNAL_TOKEN`.

## Needs from the backend

The `/internal/v1` endpoints (contract 3.1 rows 5–9 and tools in 3.2) are not in the backend yet.
Until they exist, runs fail with `MODEL_ERROR` (backend unreachable) and the backend should run FALLBACK.
