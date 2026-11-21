# services/ai — agent runtime

Code: `services/ai`. Commands below run from that folder.

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
| POST | `/v1/documents/extract` | Internal (token): invoice/notice PDF → structured fields, each with its printed text and box; see below |
| POST | `/v1/documents/text` | Internal (token): PDF/image/TXT → plain text, OCR for scans |
| POST | `/v1/rag/answer` | Internal (token): answer from records the backend retrieved, with citations; see `docs/RAG_HANDOFF.md` |
| POST | `/v1/chat` | `{question, language?, history?}` → `{answer, source, tools_used, fallback_reason}` |

`/v1/agent/runs` and `/v1/explain` need `Authorization: Bearer $INTERNAL_TOKEN` when `INTERNAL_TOKEN` is set.
`/v1/chat` needs no token: the web app calls it as `/api/v1/chat` through nginx, and it only reads.

## Structured extraction (invoices, rent adjustments, notices)

`POST /v1/documents/extract` — contract in `docs/contracts/DATA_CONTRACTS.md` §3. The backend sends
`{document_id, filename, content_base64, known_counterparties}` and waits up to 30 s (we answer within 27 s).

The model never writes numbers or boxes. The PDF text layer (pypdfium2, a box per character) is shown to it
as numbered lines; for each field it returns the line id and the value **exactly as printed**. Code then
finds that text (whole-value match, left to right within a line item), takes the union of its character
boxes (PDF points, top-left origin) and parses the value from the printed text: money → integer cents
string, quantity/unit price → Decimal string, VAT rate → basis points, dates → `YYYY-MM-DD`
(`src/ai/parsing.py`, German formats first). The document is untrusted data: it sits in a `<document>`
block, the output is schema-bound JSON, and an injected value can only be text that is printed anyway.

Before answering, the same checks as the backend run: quantity × unit price = net per line (half-up),
lines sum to the net total, VAT per rate ±1 cent, net + VAT = gross exactly, required fields per
`doc_type`, counterparty matched only if the printed name contains a known name or alias. Failures get
one correction round; what is still wrong comes back as `status: "FAILED"` with `errors` (HTTP 200).

Unreadable input is HTTP 422 `{"detail": {"code", "message"}}`: `INVALID_BASE64`, `EMPTY_FILE`,
`FILE_TOO_LARGE` (> 15 MB), `NOT_A_PDF`, `PDF_UNREADABLE` (encrypted/damaged), `TOO_MANY_PAGES` (> 20),
`NO_TEXT_LAYER` (scans are out of scope here; `/v1/documents/text` can OCR them).

Tests: `tests/test_extraction.py` (scripted model, fixtures in `tests/fixtures/extract`, regenerate with
`make_pdfs.py`) and `tests/test_extract_live.py` against the real model (skipped without `LLM_BASE_URL`;
`EXTRACT_FIXTURES` points it at another folder of PDFs + `expected.json`). With Qwen3,
`LLM_ENABLE_THINKING=false` makes answers much faster.

## Reading contracts (PDF, scans, photos)

`POST /v1/documents/text` (internal, token; multipart `file`, optional `force_ocr`) returns the text of a PDF, PNG/JPEG or TXT
and, per page, whether it came from the PDF text layer or OCR (with a confidence). Pages with a text layer are
exact (pypdfium2); scans and photos go through OCR (RapidOCR + PP-OCRv6 small ONNX models, CPU, inside the pod —
no document leaves the cluster). Limits: 15 MB, 20 pages, one OCR job at a time, 90 s.

The OCR models are not in git: `scripts/fetch_ocr_models.py` extracts them from the `onnxocr` wheel on PyPI into
`models/ocr/` (the Dockerfile does this in a build stage). Without them, scans fall back to RapidOCR's bundled
models (`ocr_model: "bundled-v3 (low accuracy)"`). Measured on our fixtures: all key phrases (amounts, names,
dates, ß/§) read correctly, ~4–5 s and ~800 MB peak per scanned page — the ai pod limit is 1536Mi.

Local setup:

```bash
pip install -r requirements-dev.txt
pip install --no-deps rapidocr_onnxruntime==1.2.3
pip install onnx && python scripts/fetch_ocr_models.py
```

## Chat (owner questions)

"Why a credit line?", "When is my cash lowest?", "What if I approve the CHF move?" — the model answers with
read-only tools over the backend's **public** API (`API_PUBLIC_URL`): `get_overview`, `get_weekly_forecast`,
`get_days_around`, `list_recommendations`, `list_payments`, `get_rules`, `get_latest_run`. Amounts reach the
model already formatted ("20,000 €"). Every number in the answer must appear in a tool result or the question;
otherwise the model gets one correction, then a safe reply is returned (`source: FALLBACK`). `<think>` blocks
are stripped. At most 2 chats at a time (one GPU), else 429. What-if for moves that are not recommendations is
not simulated (the backend tools only run inside an agent run).

## MLflow tracing and evaluation

Set `MLFLOW_TRACKING_URI` (e.g. `http://mlflow:8080`, see `deploy/openshift/mlflow/`) and every agent run, chat
and their LLM/tool calls become one MLflow trace in experiment `MLFLOW_EXPERIMENT` (default `vero-agent`),
tagged with the backend `run_id`, `status`, `failure_reason`, `model`, `prompt_version`. Export is async: a
slow or down MLflow never delays a run. Empty URI = off.

`scripts/eval_agent.py` runs the June-closure scenario N times through the real backend and reports the
AGENT success rate, fallback reasons, latency, tool calls and consistency; `--mlflow` logs it as an MLflow run:

```bash
python scripts/eval_agent.py --api http://localhost:8090/api/v1 -n 10 --mlflow http://localhost:5000
```

It calls `POST /demo/reset`, so never run it during the live demo.

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
