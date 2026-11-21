# Vero: architecture and sequence diagrams (Phase 1, Vero Cash)

All diagrams match [`contracts/DATA_CONTRACTS.md`](contracts/DATA_CONTRACTS.md). Numbers come from the demo scenario in [`DESIGN.md` §8](DESIGN.md#8-reference-demo-scenario-all-teams-test-against-these-numbers) (Pizzeria Amalfi, "today" = 5 May 2025).

**Design choice (Option B):** the **backend owns all data and domain logic** (statement import, revenue model, forecast, planner, credit sizing, policy, execution, receipts) and **hosts the agent's tools**. The **AI service is a thin agent runtime**: prompts, the tool-calling loop, the LLM and explanation texts. The agent reaches data only through read-only tools and can never execute anything. If the AI service fails, the backend finishes the run on its own (FALLBACK).

---

## 1. Architecture

### 1.1 System context

```mermaid
flowchart LR
  owner(["Owner<br/>Vincenzo, Pizzeria Amalfi"])
  stmt[/"Bank statement<br/>CSV (EUR + CHF)"/]
  vero["Vero<br/>AI finance agent"]
  model[("Open-weights LLM<br/>vLLM on OpenShift AI")]
  bank["Bank: payments, credit line<br/>(SIMULATED)"]

  owner -- "plans (e.g. June closure), rules,<br/>approves / rejects" --> vero
  vero -- "cash outlook, recommendations,<br/>receipts" --> owner
  stmt -- "import" --> vero
  vero -- "tool-calling, explanations" --> model
  vero -. "postpone transfer, credit line<br/>(simulated, never real)" .-> bank
```

### 1.2 Containers

```mermaid
flowchart LR
  subgraph browser["Browser"]
    web["apps/web<br/>React + TypeScript"]
  end

  subgraph cluster["OpenShift AI project"]
    api["services/api<br/>FastAPI<br/>public /api/v1 + internal /internal/v1 + MCP"]
    ai["services/ai<br/>agent runtime (internal)"]
    db[("PostgreSQL<br/>all state")]
    llm[("vLLM model server<br/>open-weights LLM")]
  end

  web -- "HTTPS JSON /api/v1<br/>(poll /runs/{id}, CSV upload)" --> api
  api -- "SQL" --> db
  api -- "start run, explain<br/>(/v1/agent/runs, /v1/explain)" --> ai
  ai -- "tool calls, complete / fail<br/>(/internal/v1, Bearer token)" --> api
  ai -- "OpenAI-compatible<br/>/v1/chat/completions" --> llm
```

| Container | Owns | Never does |
|---|---|---|
| **web** | Screens, CSV upload, user input, polling, display formatting | Calculate money, decide, call anything but `/api/v1` |
| **api** | Database, statement import, revenue model, engine (forecast, planner, credit sizing), tools, mandate and policy → lanes, simulated execution, receipts, run orchestration, fallback | Let the model authorize anything; expose `/internal` to the browser |
| **ai** | Prompts, agent loop, LLM calls, explanation texts + grounding check | Touch the database, compute money, decide lanes, execute |
| **db** | Source of truth | Be reachable from outside the cluster |
| **llm** | Text generation, tool-call selection | Be reachable from outside the cluster |

### 1.3 Components

```mermaid
flowchart TB
  subgraph API["services/api"]
    direction TB
    public["Public routers /api/v1<br/>overview · forecast · backtest · transactions · outflows · revenue-model<br/>imports · planned-events · mandate · events · runs · recommendations · receipts · audit · cost-memory · documents · demo · health"]
    internal["Internal routers /internal/v1<br/>tools · runs/{id}/notes · complete · fail<br/>+ MCP server /internal/mcp (SHOULD)"]
    importer["Statement importer<br/>CSV parse · CHF → EUR · classify · recurring detection"]
    orchestrator["Run orchestrator<br/>start run · 60 s watchdog · finalize"]
    toolhost["Tool host<br/>get_mandate · get_forecast · explain_drivers · get_revenue_model<br/>list_outflows · find_reschedule_options · simulate_moves<br/>size_credit_line · get_fx_exposure · check_policy"]
    engine["Deterministic engine<br/>revenue model · forecast · greedy planner · credit-line sizing · backtest"]
    policy["Policy engine<br/>mandate R1–R5 → lane"]
    fallback["Fallback runner<br/>forecast → planner → credit"]
    executor["Simulated executor<br/>('bank' adapter)"]
    receipts["Receipt writer"]
    trace["Trace writer"]
    aiclient["AI client"]
    repo["Repositories (SQLAlchemy)"]

    public --> importer --> repo
    public --> orchestrator
    public --> policy
    public --> repo
    internal --> toolhost
    internal --> orchestrator
    toolhost --> engine
    toolhost --> policy
    toolhost --> trace
    orchestrator --> aiclient
    orchestrator --> policy
    orchestrator --> executor
    orchestrator --> receipts
    orchestrator --> fallback --> engine
    engine --> repo
    executor --> repo
    receipts --> repo
    trace --> repo
  end

  subgraph AI["services/ai"]
    direction TB
    endpoints["HTTP endpoints<br/>/v1/agent/runs (202) · /v1/explain · /v1/mandate/draft · /health"]
    agent["Agent loop (background)<br/>LLM picks tools, ≤ 8 calls, 60 s deadline"]
    toolclient["Tool client<br/>REST /internal/v1/tools or MCP"]
    explainer["Explainer<br/>LLM text + grounding check + templates"]
    llmclient["LLM client<br/>live · replay · stub"]

    endpoints --> agent
    endpoints --> explainer
    agent --> llmclient
    agent --> toolclient
    explainer --> llmclient
  end

  aiclient -- "start run, explain" --> endpoints
  toolclient -- "tool calls, complete / fail" --> internal
```

### 1.4 Deployment on OpenShift AI (hard criteria: app and model both run there)

```mermaid
flowchart LR
  user(["Browser"]) -- "HTTPS" --> route["Route<br/>(only public entry)"]

  subgraph ns["OpenShift AI project (namespace)"]
    route --> websvc["Service web"] --> webpod["Deployment web<br/>nginx: static app + proxy /api/v1"]
    webpod -- "/api/v1/* only" --> apisvc["Service api"] --> apipod["Deployment api"]
    apipod --> dbsvc["Service db"] --> dbpod[("PostgreSQL<br/>+ PersistentVolume")]
    apipod -- "start run, explain" --> aisvc["Service ai"] --> aipod["Deployment ai"]
    aipod -- "tools, callbacks<br/>/internal/v1" --> apisvc
    aipod --> modelsvc["Model endpoint"] --> vllm[("vLLM serving runtime<br/>open-weights model, GPU")]
  end
```

- Only the web Route is public. nginx proxies **only `/api/v1/*`**, so `/internal/*` and `/internal/mcp` are never reachable from the browser.
- Configuration (Secret/ConfigMap):
  - api: `DATABASE_URL`, `AI_URL`, `INTERNAL_TOKEN`, `DEMO_MODE`, `DEMO_AS_OF_DATE=2025-05-05`, `STATEMENT_SOURCE=csv|db`
  - ai: `API_INTERNAL_URL`, `INTERNAL_TOKEN`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_MODE`

### 1.5 Who owns which data

```mermaid
flowchart LR
  subgraph backend["services/api (owns and stores)"]
    tx[Transaction] --- rm[RevenueModel] --- out[Outflow]
    company[Company] --- mandate[Mandate] --- pe[PlannedEvent]
    forecast[Forecast + Backtest] --- plan[PlanResult] --- credit[CreditLine]
    run[Run + TraceStep] --- rec[Recommendation] --- receipt[Receipt]
  end
  subgraph ai["services/ai (produces, never stores)"]
    proposal[Proposal]
    notes[Agent notes]
    texts[Explanation texts]
  end
  backend -- "tool results (read-only)" --> ai
  ai -- "proposals, notes, texts" --> backend
```

### 1.6 State machines

**Run**
```mermaid
stateDiagram-v2
  [*] --> QUEUED: event or import
  QUEUED --> RUNNING: AI accepted the run
  QUEUED --> RUNNING_FALLBACK: AI service unreachable
  RUNNING --> FINALIZING: AI calls /complete
  RUNNING --> RUNNING_FALLBACK: AI calls /fail or deadline passed
  RUNNING_FALLBACK --> FINALIZING: backend planner + credit done
  FINALIZING --> SUCCEEDED: policy, execution, texts, receipts stored
  FINALIZING --> FAILED: technical error (DB)
  SUCCEEDED --> [*]
  FAILED --> [*]
  note right of SUCCEEDED
    Public status shows only QUEUED, RUNNING, SUCCEEDED, FAILED.
    mode = AGENT or FALLBACK.
    A remaining cash gap is a SUCCESSFUL result.
  end note
```

**Recommendation**
```mermaid
stateDiagram-v2
  [*] --> EXECUTED: lane DONE_WITHIN_MANDATE (simulated)
  [*] --> PENDING: lane NEEDS_APPROVAL
  [*] --> BLOCKED: lane NOT_ALLOWED
  PENDING --> APPROVED_EXECUTED: owner approves (simulated)
  PENDING --> REJECTED: owner rejects
  PENDING --> STALE: data changed since proposal
  PENDING --> SUPERSEDED: newer run replaced it
```

---

## 2. Sequence diagrams

### 2.1 Import the bank statement (seed / reset uses the same path)

```mermaid
sequenceDiagram
  autonumber
  actor O as Owner
  participant W as web
  participant A as api
  participant DB as PostgreSQL

  O->>W: upload pizzeria-amalfi-bank-statement.csv
  W->>A: POST /api/v1/imports/bank-statement (multipart) + Idempotency-Key
  A->>A: parse (semicolon separator, decimal comma, dd.mm.yy)
  A->>A: CHF rows: amount × rate from reference (4 166,67 CHF @ 1,040 = 4 333,34 €)
  A->>A: classify (card revenue, variable costs, rent, payroll, taxes, partner transfer…)
  A->>A: keep bookings up to as_of_date − 1 (4 May 2025), detect recurring payments
  A->>A: revenue model: weekday averages, last 8 weeks
  A->>DB: TX: transactions, recurring payments, future outflows, revenue model, event STATEMENT_IMPORTED, run QUEUED
  A-->>W: 202 {import_id, run_id, transactions_imported}
  Note over A: the run continues as in 2.3 (forecast, plan, lanes)
```

### 2.2 Open the dashboard

```mermaid
sequenceDiagram
  autonumber
  actor O as Owner
  participant W as web
  participant A as api
  participant DB as PostgreSQL

  O->>W: open Vero
  W->>A: GET /api/v1/overview
  A->>DB: company, mandate, latest forecast, recommendations, latest run
  A-->>W: Overview
  W->>A: GET /api/v1/forecast
  A-->>W: Forecast (91 days, drivers, assumptions)
  W-->>O: "Within your limits for 13 weeks. Lowest −396 € on 1 Jun." + chart
```

### 2.3 Owner plans the June closure → agent run → recommendations (main flow)

```mermaid
sequenceDiagram
  autonumber
  actor O as Owner
  participant W as web
  participant A as api
  participant DB as PostgreSQL
  participant AI as ai service
  participant L as LLM (vLLM)

  O->>W: "We close 1–30 June"
  W->>A: POST /api/v1/events {CLOSURE_PLANNED, 2025-06-01..2025-06-30} + Idempotency-Key
  A->>DB: TX: planned event, event, run(QUEUED), financial_version+1
  A-->>W: 202 {run_id}
  A->>AI: POST /v1/agent/runs {run_id, event, company, limits}
  AI-->>A: 202 accepted
  A->>DB: run RUNNING, start 60 s deadline

  par owner watches the live trace
    loop every 500 ms until SUCCEEDED / FAILED
      W->>A: GET /api/v1/runs/{run_id}
      A-->>W: Run {status, trace so far}
    end
  and agent works (see 2.4)
    AI->>A: GET /internal/v1/tools
    loop ≤ 8 tool calls
      AI->>L: chat + tools + results so far
      L-->>AI: tool call
      AI->>A: POST /internal/v1/tools/{name}
      A->>A: run tool (engine / policy preview)
      A->>DB: append TraceStep (plain label)
      A-->>AI: ToolResult
    end
    AI->>A: POST /internal/v1/runs/{run_id}/complete {proposals}
  end

  A->>A: validate proposals against this run's candidates / credit sizing
  A->>A: policy: CHF-06 move → DONE_WITHIN_MANDATE, credit line → NEEDS_APPROVAL
  A->>A: simulated executor: CHF-06 15 Jun → 30 Jul
  A->>DB: TraceSteps (POLICY, EXECUTE)
  A->>A: engine: forecast after + "after if approved" for the credit line
  A->>AI: POST /v1/explain {facts}
  AI->>L: headline + item texts
  AI->>AI: grounding check, else templates
  AI-->>A: ExplainResult
  A->>DB: TX: forecasts, recommendations, receipts, headline, run SUCCEEDED (AGENT)

  W->>A: GET /api/v1/overview
  A-->>W: lanes: 1 DONE_WITHIN_MANDATE, 1 NEEDS_APPROVAL
  W-->>O: "Closing in June takes you past your overdraft from 15 Jun. I postponed Luigi's June transfer, and a 20 000 € line would cover the rest."
```

### 2.4 Inside the agent loop (tools through the backend)

```mermaid
sequenceDiagram
  autonumber
  participant AG as Agent loop (ai)
  participant L as LLM (vLLM)
  participant A as api (tool host)

  AG->>A: GET /internal/v1/tools
  A-->>AG: ToolSpec[]
  AG->>L: system prompt + event "closure 1–30 June" + tools
  L-->>AG: get_forecast()
  AG->>A: POST /tools/get_forecast
  A-->>AG: beyond −5 000 € from 15 Jun, lowest −22 761 € on 3 Jul, driver: no revenue 1–30 Jun
  L-->>AG: find_reschedule_options()
  AG->>A: POST /tools/find_reschedule_options
  A-->>AG: CHF-06 15 Jun → 30 Jul (first breach +12 days), resolved = false
  L-->>AG: size_credit_line(after_moves: CHF-06)
  AG->>A: POST /tools/size_credit_line
  A-->>AG: 20 000 € from 27 Jun (shortfall 16 081,11 €)
  L-->>AG: check_policy(CHF-06 move)
  AG->>A: POST /tools/check_policy
  A-->>AG: lane_preview DONE_WITHIN_MANDATE (≤ 5 000 €)
  L-->>AG: final answer: postpone CHF-06, propose credit line
  AG->>A: POST /runs/{run_id}/complete {proposals}

  Note over AG,A: Invalid tool call → INVALID_ARGUMENT (agent may retry within its limit).<br/>STALE_RUN → agent stops and calls /fail.
```

### 2.5 Fallback: model or AI service unavailable

```mermaid
sequenceDiagram
  autonumber
  participant A as api
  participant AI as ai service
  participant DB as PostgreSQL

  alt AI unreachable at start
    A-xAI: POST /v1/agent/runs (timeout / error)
  else AI gives up
    AI->>A: POST /internal/v1/runs/{run_id}/fail {reason}
  else deadline (60 s) passes without /complete
    A->>A: watchdog fires, later callbacks are rejected (RUN_CLOSED)
  end
  A->>DB: TraceStep FALLBACK (e.g. "The model took too long, using standard checks", data.reason)
  A->>A: engine: forecast → planner → credit sizing
  A->>A: policy, execution, re-forecast (same as 2.3)
  A->>AI: POST /v1/explain (best effort, 10 s)
  alt explain fails
    A->>A: backend templates
  end
  A->>DB: run SUCCEEDED (mode FALLBACK)
```

### 2.6 Owner approves the credit line

```mermaid
sequenceDiagram
  autonumber
  actor O as Owner
  participant W as web
  participant A as api
  participant DB as PostgreSQL

  O->>W: Approve "Credit line 20 000 € from 27 Jun (illustrative)"
  W->>A: POST /api/v1/recommendations/{id}/approve {note?} + Idempotency-Key
  A->>DB: load recommendation (lock)
  alt not PENDING
    A-->>W: 409 NOT_PENDING
  else data changed since proposal
    A->>DB: status STALE
    A-->>W: 409 STALE_RECOMMENDATION
  else valid
    A->>A: policy re-check (R5: credit enabled, ≤ 30 000 €)
    A->>A: simulated executor, confirmation_ref SIM-…
    A->>DB: credit line active from 27 Jun, financial_version+1
    A->>A: engine: recompute (no day beyond limit, headroom 3 918,89 € at lowest)
    A->>DB: TX: APPROVED_EXECUTED, DECISION receipt, forecast
    A-->>W: DecisionResult {forecast_summary, receipt_id}
    W-->>O: chart within limits, "Approved by you (simulated)"
  end
```

### 2.7 Owner rejects, or asks for something not allowed

```mermaid
sequenceDiagram
  autonumber
  actor O as Owner
  participant W as web
  participant A as api
  participant DB as PostgreSQL

  O->>W: Reject the credit line
  W->>A: POST /api/v1/recommendations/{id}/reject + Idempotency-Key
  A->>DB: REJECTED, DECISION receipt
  A-->>W: DecisionResult (gap stays: beyond limit from 27 Jun, 38 days)
  W-->>O: warning stays visible

  O->>W: "Can we pay June salaries later?"
  W->>A: POST /api/v1/outflows/{PAY-06}/reschedule-requests {to_date: 2025-07-15}
  A->>A: run → policy: R4 protected (PAYROLL)
  A->>DB: Recommendation NOT_ALLOWED + receipt
  A-->>W: 202 {run_id} → result: NOT_ALLOWED "Payroll is protected by your rules"
```

### 2.8 Owner changes the mandate

```mermaid
sequenceDiagram
  autonumber
  actor O as Owner
  participant W as web
  participant A as api
  participant DB as PostgreSQL
  participant AI as ai service

  O->>W: open Rules
  W->>A: GET /api/v1/mandate
  A-->>W: Mandate v1 (act alone: off)

  opt SHOULD: describe rules in words
    O->>W: "You may postpone transfers under 5 000 € yourself"
    W->>A: POST /api/v1/mandate/draft-from-text {text}
    A->>AI: POST /v1/mandate/draft {text, current}
    AI-->>A: {changes, uncertain, unsupported, source}
    A->>A: merge + validate
    A-->>W: MandateDraft {draft, diff}
  end

  O->>W: edit / confirm
  W->>A: POST /api/v1/mandate {MandateIn} + Idempotency-Key
  A->>A: validate (PAYROLL, SOCIAL_SECURITY, TAX must stay protected)
  A->>DB: insert version 2 (DRAFT)
  A-->>W: Mandate v2 DRAFT
  O->>W: Activate
  W->>A: POST /api/v1/mandate/2/activate {expected_active_version: 1}
  A->>DB: v1 SUPERSEDED, v2 ACTIVE
  A-->>W: Mandate v2 ACTIVE
```

### 2.9 Demo reset

```mermaid
sequenceDiagram
  autonumber
  actor P as Presenter
  participant W as web
  participant A as api
  participant DB as PostgreSQL

  P->>W: Reset
  W->>A: POST /api/v1/demo/reset
  alt DEMO_MODE off
    A-->>W: 403
  else
    A->>DB: TX: truncate business tables
    A->>A: import sample statement up to 4 May 2025 (as in 2.1), as_of 5 May 2025, mandate v1
    A->>A: engine: baseline forecast (S0)
    A->>DB: store
    A-->>W: {reset: true, as_of_date: 2025-05-05, financial_version: 1}
    W->>A: GET /api/v1/overview
    A-->>W: green dashboard
  end
```

### 2.10 Backtest overlay (demo)

```mermaid
sequenceDiagram
  autonumber
  participant W as web
  participant A as api

  W->>A: GET /api/v1/forecast/backtest
  A->>A: actual 2025 daily balances after as_of (from the full sample file)
  A-->>W: Backtest {days[forecast, actual], forecast_lowest 3 Jul −22 760,82 €, actual_lowest 3 Jul −22 097,60 €}
  W-->>W: overlay "what really happened" on the chart (labelled backtest)
```

### 2.11 Health and model check (hard criterion #2)

```mermaid
sequenceDiagram
  autonumber
  participant W as web
  participant A as api
  participant AI as ai service
  participant L as LLM (vLLM)

  W->>A: GET /api/v1/health/model
  A->>AI: GET /health/model
  AI->>L: tiny completion ("pong")
  alt reachable
    AI-->>A: {ok: true, model, mode: live, latency_ms}
    A-->>W: 200 ModelHealth
  else unreachable
    AI-->>A: {ok: false, error}
    A-->>W: 503 ModelHealth
  end
```
