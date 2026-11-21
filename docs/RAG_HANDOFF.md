# RAG question answering: AI team handoff

This feature answers questions about booked bank transactions and scheduled payments.
It is separate from the existing cash-flow **agent run** and **explanation** endpoints.
The backend owns the financial state; the AI service owns model calls. The model does
not query PostgreSQL or issue agent tool calls for this path.

## Request path

```text
Browser (the web app)
  POST /api/v1/assistant/ask  {question}
    API (routers/rag.py)
      repository.read() → state.history, state.outflows, state.inflows
      rag.retrieve() → up to 12 matching Source records
      POST AI_URL/v1/rag/answer  {question, sources}
        AI (ai/rag.py) → vLLM OpenAI-compatible chat completion
        validate model JSON, source IDs, answer length and numbers
        (one correction round; otherwise list the records: answer_source TEMPLATE)
      ← {answer, sources, model, answer_source, fallback_reason}
  ← answer and cited records
```

`state.history` contains only booked transactions before the demo `as_of` date. Outflows
and inflows are future scheduled items. The state loader uses either the configured CSV
or the PostgreSQL statement table; retrieval itself never makes a database query.

## Where to edit

| Concern | File |
|---|---|
| Public request and response | `services/api/src/api/routers/rag.py` |
| Record construction, Turkish aliases, ranking | `services/api/src/api/rag.py` |
| API → AI HTTP call and timeout | `services/api/src/api/agent.py`, `services/api/src/api/config.py` |
| Grounding prompt, response validation | `services/ai/src/ai/rag.py` |
| LLM transport | `services/ai/src/ai/llm.py` |
| Internal route and token check | `services/ai/src/ai/main.py` |
| Public OpenAPI schema | `docs/contracts/openapi.json` |

## API contract

Public request:

```http
POST /api/v1/assistant/ask
Content-Type: application/json

{"question":"Kira ödemeleri ne zaman?"}
```

Successful response:

```json
{
  "answer": "...",
  "sources": [{
    "id": "record-uuid",
    "kind": "transaction",
    "date": "2025-04-01",
    "amount_cents": "-380000",
    "currency": "EUR",
    "category": "RENT",
    "counterparty": "Main Street Real Estate",
    "description": "RENT PAYMENT April rent"
  }],
  "model": "vero-llm",
  "answer_source": "MODEL",
  "fallback_reason": null
}
```

`amount_cents` is a **signed integer string**, so `-380000` means a 3,800 EUR outflow.
`kind` is `transaction`, `outflow`, or `inflow`. An empty match returns a local
"no matching records" answer with `sources: []`; no model call is made. The endpoint
does not fail on model trouble: if the model answer fails the checks twice, times out,
or the AI service cannot be reached, the answer lists the (up to 5) most relevant
retrieved records instead, with `answer_source: "TEMPLATE"` and a `fallback_reason`.
The UI can show `MODEL` answers as text and `TEMPLATE` answers as a record list.

The internal AI endpoint is `POST /v1/rag/answer`. Its request adds `sources` to the
public question and requires the shared `INTERNAL_TOKEN` when configured. The model is
asked for JSON with `answer` and `source_ids`. Only source IDs present in the request
are accepted and returned. The model receives amounts and dates already formatted in
the question's language (Turkish or English), e.g. `-3.800 €` and `1 Nisan 2025`, and is
told to copy them. Every number in the answer must match a cited record's amount or date
in a common spelling (`3.800,00 €`, `3,800.00 EUR`, `01.04.2025`, `1 April 2025`) or a
number from the question; a self-computed total (e.g. two rents added up) is rejected.
A rejected answer gets one correction round. This is a guardrail, not a proof that
every sentence is true.

## Retrieval and current limits

Retrieval is deterministic lexical ranking, with aliases such as Turkish `kira` →
`rent`. It looks for overlapping terms in category, counterparty, description, date,
and exact euro or cent amount. It returns at most 12 records. It does **not** use
embeddings or a vector index. It is suitable for questions about specific payments,
dates and counterparties; it does not provide complete totals across a large period.
For aggregates, add a deterministic backend query and pass its result as evidence.

The model receives record descriptions as untrusted data. It must not treat text inside
a bank reference as an instruction. The existing agent's action and approval rules
remain independent; this endpoint is read-only.

## Local verification

1. Install `services/api/requirements.txt` and `services/ai/requirements-dev.txt` in
   a Python environment (Python 3.12 or later).
2. Run `pytest -q services/api/tests/test_rag_api.py services/api/tests/test_openapi.py`
   and `pytest -q services/ai/tests` with both service `src` directories importable.
3. Regenerate the public contract after endpoint changes with `make schema` (or
   `python -m api.openapi` from `services/api`).
4. For a real model smoke test, set `LLM_MODE=live`, `LLM_BASE_URL` and `LLM_MODEL`.
   Set the same `INTERNAL_TOKEN` for API and AI, and point API `AI_URL` at the AI
   service. Ask a question through port 8080 and check the answer's source IDs.

The existing `services/ai/scripts/smoke_llm.py` checks model connectivity only; it
does not exercise this RAG endpoint. Model quality still needs testing against the
actual bank statement and deployed vLLM service.

## Changes after review (21 November)

- The number check only accepted the raw ISO date and `3800.00`; natural answers such as
  "1 Nisan 2025, 3.800,00 €" were rejected, so a real model would mostly get 503. Records
  now go to the model pre-formatted and the check normalises spellings.
- 503 replaced by the record-list fallback (AI side and API side).
- Language detection no longer treats every `i` as Turkish (`re.I` made `ı` match `i`).
- The fallback keeps the backend's relevance order; English filler words (did, pay, much…)
  are stop words, so "How much did I pay City Energy?" no longer matches payroll.
- `apps/web/Dockerfile` and `apps/web/index.html` were left out: the web app is the
  React/Vite frontend; it can call `POST /api/v1/assistant/ask` directly.

## Verification in this workspace

On 21 November 2026, the RAG route, retrieval, model validation, OpenAPI and AI
service test selection passed: **32 tests**. The broader API run (excluding the two
PostgreSQL integration modules) reported 162 passed, 153 skipped and two unrelated
environment failures: the repository has no sample bank CSV, and a local closed port
returned `ConnectTimeout` where an existing health test expects `ConnectError`.
No live vLLM or PostgreSQL end-to-end test was possible in this workspace.
