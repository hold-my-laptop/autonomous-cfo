# Backend (`services/api`): features to implement

Phase 1, Vero Cash. Every feature traces back to [`DESIGN.md`](DESIGN.md) (rules, scenario numbers), [`contracts/DATA_CONTRACTS.md`](contracts/DATA_CONTRACTS.md) (fields, endpoints) and [`ARCHITECTURE.md`](ARCHITECTURE.md) (sequences).

**Priority:** **MUST** = the demo fails without it · **SHOULD** = after the demo path works end to end · **MIGHT** = only if time remains.
**Order:** build top to bottom. Each block ends in something runnable and testable.

---

## Milestone A: cash flow and forecast (deterministic core, no AI)

### B01 · Project setup — MUST
- FastAPI app under `src/api`, entrypoint in `pyproject.toml` (`[tool.fastapi] entrypoint = "api.main:app"`), run with `uv run fastapi dev`.
- Settings via environment (`pydantic-settings`): `DEMO_AS_OF_DATE=2025-05-05`, `STATEMENT_PATH`, `OPENING_BALANCE=250000`, `DATABASE_URL`, `AI_URL`, `INTERNAL_TOKEN`, `DEMO_MODE`.
- Dev tools: pytest, ruff.
- **Done when:** `GET /api/v1/health/live` returns `{"status":"ok"}` and `uv run pytest` runs.

### B02 · Money and dates — MUST
- Money is an integer number of **cents** everywhere. JSON serializes it as a string (`"125050"`), and input accepts only `^-?[0-9]{1,15}$` (15 digits keep every value and sum within PostgreSQL BIGINT).
- Parse German amounts (`-4166,67`, `275,35`) without floats.
- Parse dates in `dd.mm.yy` format.
- Calendar days only.
- **Done when:** unit tests cover parsing (`"-4166,67"` → `-416667`), serialization, and rejecting floats or invalid strings.

### B03 · Bank statement import — MUST
- Parse the CSV: `;` separator, header row, UTF-8, LF line endings.
- **CHF rows:** read the rate from the reference (`@ 1.040 EUR/CHF`). EUR = CHF × rate, rounded half-up to the cent. Keep `original_amount`, `original_currency` and `fx_rate`.
- Classify each row (see DESIGN §4.2) into `CARD_REVENUE`, `VARIABLE_COST`, `RENT`, `PAYROLL`, `SOCIAL_SECURITY`, `TAX`, `UTILITIES`, `PARTNER_TRANSFER` or `OTHER`. Direct debits are classified by counterparty (tax office, Krankenkasse, utilities).
- Opening balance from settings (2 500,00 €).
- **Done when:**
  - 1 125 rows are imported with no duplicates;
  - the 15 Dec tranche is 4 362,50 € (4 166,67 CHF @ 1,047);
  - with all rows, the derived balances are −20 098,69 € on 30 Jun, −22 097,60 € on 3 Jul and 33 266,35 € on 31 Dec.

### B04 · Recurring payment detection — MUST
- Outgoing fixed-cost rows before `as_of_date`.
- **Group key:**
  - payroll: all salary payments on the same day are summed into one "Payroll (N people)" item;
  - everything else: `(category, counterparty, transaction type)`.
- **Monthly recurring:** ≥ 3 consecutive months, amount within ±2 % (CHF compares the CHF amount), day of month within ±1.
- **Projection:** the next amount is the last observed one. CHF uses the last rate (4 166,67 × 1,040 = **4 333,34 €**). Generate future **Outflows** for each month in the window, with refs `RENT-MM`, `UTIL-MM`, `WTAX-MM`, `SOC-MM`, `PAY-MM`, `CHF-MM`.
- **Movable:** `PARTNER_TRANSFER` gets `latest_allowed_date = due + 45 days` (demo assumption). Everything else gets `null`.
- **Done when:** as of 5 May, the detected items are rent, wage tax, utilities, social security, payroll (8 600 €) and the CHF tranche. The quarterly taxes are **not** detected (they appear only once).

### B05 · Owner-entered tax schedule — MUST
- Seed the quarterly prepayments as `source: OWNER` outflows:
  - income tax 1 150 € on 10 Mar / Jun / Sep / Dec (`ITAX-MM`);
  - trade tax 225 € on 15 Feb / May / Aug / Nov (`TTAX-MM`).
- **Done when:** the 5 May – 3 Aug window contains TTAX-05 (15 May) and ITAX-06 (10 Jun), matching the outflow table in DESIGN §8.1 (20 outflows).

### B06 · Revenue model — MUST
- Window: the 8 weeks before `as_of_date` (10 Mar – 4 May 2025).
- For each weekday, sum card revenue and variable costs separately, then floor-divide by 8 (in cents).
- **Done when:** the values match DESIGN §8.1 exactly, e.g. Monday revenue 4 044,72 € and costs −1 124,71 €, Sunday 338,50 € and −231,89 €.

### B07 · Forecast engine — MUST
- Start balance = opening + all transactions before `as_of_date` (**401,96 €** on 5 May).
- 91 calendar days. Each day = revenue model (0 on closure days) + fixed outflows + other inflows. Approved credit lines add `credit_available`.
- `below_floor = closing + credit_available < cash_floor`.
- **Summary:** `first_breach` (with shortfall), `lowest`, `days_below_floor`, `end_balance`, `headroom_at_lowest`.
- **Drivers:** `NO_REVENUE` for each closure, with the lost revenue amount (45 905,78 € for June). Plus up to 5 largest `OUTFLOW`s before the first breach.
- **Assumptions:** list the codes from DATA_CONTRACTS §1.
- Pure functions (snapshot in, result out) with an `engine_version` constant.
- **Done when:**
  - **S0** (no closure): no breach, lowest −396,30 € on 1 Jun, end 2 392,20 €.
  - **S1** (closure 1–30 Jun): first breach 15 Jun at −7 516,25 €, lowest −22 760,82 € on 3 Jul, 50 days, end −20 974,50 €.

### B08 · Backtest — MUST (cheap, strong demo point)
- Actual daily balances after `as_of_date` from the full-year file, compared with the forecast.
- **Done when:** the actual values are a first breach on 15 Jun at −8 848,69 €, lowest −22 097,60 € on 3 Jul, 48 days, and 3 Aug at −11 914,55 €.

### B09 · In-memory demo state + reset — MUST
- One company (Pizzeria Amalfi), mandate v1 (DESIGN §7.2), planned events, `financial_version`.
- Built by importing the CSV up to `as_of_date − 1`.
- Hidden behind a repository interface, so B20 can swap it for PostgreSQL.
- `POST /api/v1/demo/reset` (only when `DEMO_MODE`).
- **Done when:** reset twice gives an identical state and S0 forecast.

### B10 · Read API (cash flow) — MUST
Endpoints (shapes in DATA_CONTRACTS §2):

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/health/live`, `/health/ready` | status |
| GET | `/api/v1/company` | Company (as_of, balance, financial_version…) |
| GET | `/api/v1/transactions?from=&to=&category=` | Transaction[] |
| GET | `/api/v1/outflows` | Outflow[] |
| GET | `/api/v1/inflows` | Inflow[] (empty in the demo) |
| GET | `/api/v1/revenue-model` | RevenueModel |
| GET | `/api/v1/planned-events` | PlannedEvent[] |
| GET | `/api/v1/mandate`, `/mandate/versions` | Mandate |
| GET | `/api/v1/forecast` (`?weekly=true`) | Forecast (+ 13 weekly buckets) |
| GET | `/api/v1/forecast/backtest` | Backtest |

- **Done when:** a TestClient test reads S0 through `/forecast`.

### B11 · Errors, idempotency, versions — MUST
- Errors as `application/problem+json` with a `code` (DATA_CONTRACTS Problem). Validation errors map to `VALIDATION_ERROR`.
- `Idempotency-Key` required on state-changing POSTs:
  - the same key and body replays the stored response;
  - a different body returns `IDEMPOTENCY_KEY_REUSED`.
- `expected_financial_version` is checked, returning `VERSION_CONFLICT` with the current version.
- **Done when:** tests cover a missing key, a replay, key reuse and a stale version.

### B12 · Closure event — MUST
- `POST /api/v1/events` with `CLOSURE_PLANNED` stores the PlannedEvent and bumps `financial_version`. Until the agent exists, the response is `202 {run_id}` and the run is completed by the backend in FALLBACK mode (see B19).
- **Done when:** after posting closure 1–30 Jun, `GET /forecast` returns S1.

**→ Milestone A demo:** green forecast → post the closure → red forecast with drivers → backtest overlay. No AI yet.

---

## Milestone B: decisions (planner, credit, policy, recommendations)

### B13 · Mandate management — MUST
- `POST /mandate` (DRAFT), `POST /mandate/{v}/activate` (with `expected_active_version`), `POST /mandate/pause` and `/resume`.
- Validation: `PAYROLL`, `SOCIAL_SECURITY` and `TAX` must stay protected, otherwise `MANDATE_INVALID`. `max_amount_per_action` must be ≤ `max_amount_per_day`.
- **Done when:** v1 → v2 (act alone on, 5 000 / 5 000 €) works, and removing `TAX` returns 422.

### B14 · Planner (greedy) — MUST
- Candidates: movable, not protected, not already moved, `date ≤ first_breach < latest_allowed_date`. The target date is kept inside the forecast window.
- **Minimum benefit:** use a candidate only if it delays the first breach or cuts days below the floor by **≥ 3 days**.
- **Order:** larger amount first, then the target date furthest away, then ref.
- Move it, recompute, repeat.
- Output **PlanResult** (candidates, `resolved`, `residual_breach`, `forecast_if_all_applied`, `planner_version`).
- **Done when:** at S1 the plan is exactly `[CHF-06 15 Jun → 30 Jul]` (CHF-05 rejected as +1 day), and applying it gives S2: first breach 27 Jun at −5 832,91 €, 38 days, lowest −21 081,11 € on 2 Aug.

### B15 · Credit-line sizing — MUST
- If a gap remains after the planner: amount = shortfall rounded **up to 5 000 €**, `available_from` = first remaining breach day, `available_until` = 30 Sep 2025 (illustrative), plus `headroom_at_lowest`.
- **Done when:**
  - at S2: shortfall 16 081,11 € → **20 000 €** from 27 Jun, headroom 3 918,89 €;
  - at S1 (v1, no move): 17 760,82 € → 20 000 € from 15 Jun.

### B16 · Policy engine — MUST
- Evaluate **every** rule and record each check: R4 protected, validity (within `latest_allowed_date` and `max_delay_days`, not before today, not after the forecast window), pause, R2 act-alone, R3 per action / per decision day, R5 credit (enabled, ≤ max, **always** needs approval), R1 info.
- The strictest result wins → lane `DONE_WITHIN_MANDATE`, `NEEDS_APPROVAL` or `NOT_ALLOWED`.
- With owner approval, R2, R3 and pause are lifted. R4 and validity are not.
- Pure function with a `policy_version`.
- **Done when:**
  - v2: CHF-06 → DONE_WITHIN_MANDATE, credit line → NEEDS_APPROVAL;
  - v1: both → NEEDS_APPROVAL;
  - payroll move → NOT_ALLOWED;
  - paused → everything NEEDS_APPROVAL.

### B17 · Simulated executor + recommendations + receipts — MUST
- **Executor:** applies DONE_WITHIN_MANDATE and approved items to the state (moves `scheduled_date` or activates a credit line), with `execution: "SIMULATED"` and `confirmation_ref` `SIM-…`. It's the **only** code that changes a schedule.
- **Recommendation records:** lane, status, checks, `impact.before` / `after_if_executed`, explanation, `receipt_id`.
- **Receipts:** immutable `PROPOSAL` and `DECISION` receipts.
- Endpoints:
  - `GET /recommendations?lane=&status=`
  - `GET /recommendations/{id}`
  - `POST /recommendations/{id}/approve` and `/reject`
  - `GET /receipts/{id}`
- **Stale check:** if the data changed since the proposal, return `STALE_RECOMMENDATION`. A decision that isn't pending returns `NOT_PENDING`.
- **Done when:** approving the 20 000 € line gives S3 (no breach, headroom 3 918,89 €), and rejecting keeps S2. Both write a DECISION receipt.

### B18 · Owner reschedule request — MUST
- `POST /outflows/{id}/reschedule-requests {to_date}` → run → policy.
- **Done when:** PAY-06 → NOT_ALLOWED (R4) with a receipt.

### B19 · Runs, trace and overview — MUST
- **Run record:** status (`QUEUED` / `RUNNING` / `SUCCEEDED` / `FAILED`), mode (`AGENT` / `FALLBACK`), trigger, trace steps (`source: BACKEND`), result.
- **FALLBACK pipeline (the backend alone):** forecast → planner → credit sizing → policy → execute → re-forecast → template texts → store. Every step is written to the trace with a plain label.
- `GET /runs/{id}`, `GET /runs`, `GET /overview` (company, summary, headline, four lanes, pending count, latest run).
- **ANALYSED findings:** closure driver, recurring payments, CHF exposure (4 325,00 – 4 354,17 €, rate 1,038 – 1,045), backtest.
- **Done when:** the full demo path works through the API with no AI service: closure → run SUCCEEDED (FALLBACK) → overview shows 1 DONE_WITHIN_MANDATE + 1 NEEDS_APPROVAL → approve → S3.

**→ Milestone B demo:** the complete decision story, using backend templates for texts.

---

## Milestone C: persistence

### B20 · PostgreSQL — MUST
- SQLModel (preferred by the FastAPI skill) or SQLAlchemy, plus Alembic migrations.
- **Tables:** company, transactions, recurring payments, outflows, inflows, planned events, mandate versions, events, runs, trace steps, forecasts, recommendations, executions, receipts, idempotency keys, credit lines. Money as `BIGINT` cents.
- Swap the in-memory repository for the database one. Demo reset = truncate + re-import.
- Uses `infra/docker-compose.yml` (Postgres on `localhost:5433` locally).
- **Done when:** all tests pass against Postgres, and reset in under 5 s.

### B21 · Statement upload endpoint — SHOULD
- `POST /imports/bank-statement` (multipart CSV, size limit via `max_body_size`) → `202 {import_id, run_id, transactions_imported}`, then a `STATEMENT_IMPORTED` run.
- Invalid file → `IMPORT_INVALID`.

---

## Milestone D: agent integration (Option B)

### B22 · Internal tool API — MUST
- `/internal/v1`, `Authorization: Bearer INTERNAL_TOKEN`. **Not** exposed via nginx.
- `GET /tools` returns **ToolSpec[]**: name, LLM-oriented description, JSON Schema.
- `POST /tools/{name}` → **ToolResult**. Bound to `run_id` and its `financial_version` (`STALE_RUN`), idempotent per `call_id`, max calls per run (`TOOL_LIMIT_REACHED`). Each call writes a trace step with a plain label.
- **Tools:** `get_mandate`, `get_forecast`, `explain_drivers`, `get_revenue_model`, `list_outflows`, `find_reschedule_options`, `simulate_moves` (only this run's candidates), `size_credit_line`, `get_fx_exposure`, `check_policy` (preview only).
- **Done when:** a scripted client calling the tools in the 2.4 sequence order gets the expected results.

### B23 · Agent run orchestration — MUST
- On an event: create the run, `POST ai/v1/agent/runs` (→ 202), start the 60 s watchdog.
- `POST /internal/v1/runs/{id}/notes`, `/complete`, `/fail`.
- **Validate proposals:** moves must be this run's planner candidates, and the credit must equal this run's sizing. Anything else is discarded and noted in the trace.
- Then the same finalize path as the fallback (policy → execute → re-forecast → explain).
- Fallback on: AI unreachable, `/fail`, or the deadline passing. Late callbacks get `RUN_CLOSED`.
- **Done when:** a test with a fake AI service covers the AGENT path, invalid proposals, the timeout → FALLBACK, and `/fail` → FALLBACK.

### B24 · Explanations — MUST
- `POST ai/v1/explain` with the facts after policy and execution (10 s timeout).
- Store the headline and per-item texts with their `source`. On failure, use the backend templates (one per lane / rule, DESIGN §6).
- **Done when:** the AI down → templates and `source: TEMPLATE`.

### B25 · Model health passthrough — MUST
- `GET /api/v1/health/model` → AI service `/health/model`. Returns 200 or 503 with ModelHealth (hard criterion #2 visible in the UI).

### B26 · MCP server for the tools — SHOULD (bonus criterion)
- `/internal/mcp`, with the same tools, schemas and results as B22.

### B27 · Mandate draft from text — SHOULD
- `POST /mandate/draft-from-text` → `ai/v1/mandate/draft` → merge + validate → **MandateDraft** (diff, uncertain, unsupported). Nothing is saved.

---

## Milestone E: delivery

### B28 · OpenAPI export — MUST
- Explicit return types on every route. Export `openapi.json` for the frontend to generate types from. A test checks the exported file matches the app.

### B29 · Container + config for OpenShift — MUST (with DevOps)
- Containerfile (UBI Python, non-root), `fastapi run`.
- Env: `DATABASE_URL`, `AI_URL`, `INTERNAL_TOKEN`, `DEMO_MODE`, `DEMO_AS_OF_DATE`, `STATEMENT_PATH`.
- Readiness checks the DB.
- **Single replica only** (`replicas: 1`, `strategy: Recreate`). Agent run deadlines live in process memory, a starting pod falls back every run still `RUNNING`, and a pod whose `DEMO_AS_OF_DATE` differs from the database resets it. Two API pods must never run at once.
- The sample CSV is available in the image or a volume (**decide** whether it may be committed to `data/`).

### B30 · Scenario test — MUST (release gate)
- One test runs the whole demo through the public API:
  1. reset → S0;
  2. mandate v2;
  3. closure → run SUCCEEDED, lanes (1 DONE, 1 NEEDS_APPROVAL), S2 numbers;
  4. approve the credit line → S3;
  5. payroll request → NOT_ALLOWED;
  6. backtest values;
  7. reset → S0 again.
- It must pass in both FALLBACK and AGENT (fake AI) modes.

### B31 · Logging — SHOULD
- Structured logs with `run_id`. Never log full IBANs or the internal token.

### B32 · Forecast improvements — MIGHT
- Seasonal or recent-trend uplift after reopening (the backtest shows +15–25 % revenue in H2), shown as an alternative scenario, never silently.
- Investment suggestion for the year-end surplus (card's investment agent).
- FX hedging hint for the CHF tranches (card's FX agent).

---

## Summary

| Milestone | Features | Result |
|---|---|---|
| A · Cash flow & forecast | B01–B12 | Forecast, closure, backtest via API (no AI) |
| B · Decisions | B13–B19 | Full demo story with backend fallback |
| C · Persistence | B20–B21 | PostgreSQL, CSV upload |
| D · Agent | B22–B27 | LLM agent through tools, explanations, model health, MCP |
| E · Delivery | B28–B32 | OpenAPI, container, scenario test, logging, extras |
