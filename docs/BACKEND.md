# Backend (`services/api`): features to implement

Phase 1, Vero Cash (B01–B32), and below it [Phase 2, Vero Proof](#phase-2-vero-proof) (P01–P23). Every Phase 1 feature traces back to [`DESIGN.md`](DESIGN.md) (rules, scenario numbers), [`contracts/DATA_CONTRACTS.md`](contracts/DATA_CONTRACTS.md) (fields, endpoints) and [`ARCHITECTURE.md`](ARCHITECTURE.md) (sequences).

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
- SQLModel (preferred by the FastAPI skill). No Alembic: the tables hold only demo state that a reset rebuilds from the CSV, so a schema change bumps `SCHEMA_VERSION` in `persistence.py` and the tables are recreated at startup. Add Alembic once real data must survive schema changes.
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
- `make schema` (or `uv run python -m api.openapi` in `services/api`) writes [`contracts/openapi.json`](contracts/openapi.json); `--check` only reports whether it is out of date. Operation ids are the handler names (e.g. `get_forecast`, `approve_recommendation`), errors are typed as `application/problem+json` **Problem**, and `/internal` is not included.

### B29 · Container + config for OpenShift — MUST (with DevOps)
- Containerfile (UBI Python, non-root), `fastapi run`.
- Env: `DATABASE_URL`, `AI_URL`, `INTERNAL_TOKEN`, `DEMO_MODE`, `DEMO_AS_OF_DATE`, `STATEMENT_SOURCE`, `STATEMENT_PATH`.
- `STATEMENT_SOURCE` picks where the bank statement is read at startup and on demo reset: `csv` (default, the file at `STATEMENT_PATH`) or `db` (table `bank_statement_lines` in the `DATABASE_URL` database, see [DATA_CONTRACTS](contracts/DATA_CONTRACTS.md#bank-statement-table-statement_sourcedb)). `db` without `DATABASE_URL` is refused at startup. If the table is missing, empty or invalid, or the database is down, the state is not loaded (readiness 503, retried like a late database); the CSV is never used instead.
- Readiness checks the DB. If the database (or the statement) is not available at startup, later requests retry loading at most every `STATE_LOAD_RETRY_S` (5 s), so the pod becomes ready on its own once the database is up; interrupted agent runs are then recovered.
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
  7. the activity log (B33) verifies as a chain and lists the owner's approval and the blocked payroll request;
  8. reset → S0 again, with a new log.
- It must pass in both FALLBACK and AGENT (fake AI) modes.
- **As built:** `tests/test_scenario.py`, one test parametrized over `FALLBACK` (no `INTERNAL_TOKEN`) and `AGENT` (a fake AI service that accepts the run; the test calls the tools and `/complete` like the model). It asserts S0, the two lanes with CHF-06 → 30 Jul and the credit line of 2000000 from 27 Jun, S2 (27 Jun −583291, 38 days, lowest −2108111 on 2 Aug), S3 without a breach, the payroll request `NOT_ALLOWED`/`BLOCKED`, the backtest (S1 lowest −2276082 on 3 Jul, 50 days; actual 15 Jun −884869, lowest −2209760 on 3 Jul, 48 days; error −66322, 300 bp), the log's hash chain recomputed with the documented formula (owner approval, executed move, blocked payroll request; proposals by `AGENT` with `vero-llm` or `SYSTEM`), and a reset back to S0 with the same single first log entry. Passes in memory and on PostgreSQL.

### B31 · Logging — SHOULD
- Structured logs with `run_id`. Never log full IBANs or the internal token.

### B32 · Forecast improvements — MIGHT
- Seasonal or recent-trend uplift after reopening (the backtest shows +15–25 % revenue in H2), shown as an alternative scenario, never silently.
- Investment suggestion for the year-end surplus (card's investment agent).
- FX hedging hint for the CHF tranches (card's FX agent).

### B33 · Activity log (audit) — MUST (the frontend's Activity page)
- **What it is:** an append-only log of everything that changed state, in plain language, for the owner and the judges. Each entry is sealed with a SHA-256 hash chained to the entry before, so the browser can recompute every hash and show that nothing was changed or removed afterwards. It complements the receipts (B17), which hold the full evidence for each recommendation.
- **Actors:** `OWNER` (planned a closure, asked to move a payment, approved or rejected, mandate saved, activated, paused or resumed), `AGENT` (proposals and the run headline when the model ran the run, with the model name), `RULES` (what the policy did with a proposal: done within the mandate, or not allowed), `SYSTEM` (demo load, backend proposals in FALLBACK, fallback reasons, superseded or resized proposals, a decision refused because the data changed). Entries carry the agent (`CASH`; Phase 2 adds `PROOF`), run, recommendation, lane and `execution: SIMULATED` where they apply.
- **Written where the state changes**, in the same write as the change, so a rolled-back request leaves no entry and an idempotent replay adds none. A demo reset starts a new log whose first entry is fixed (5 May 2025, 08:00 UTC), so two resets give the same first hash.
- **Storage:** `DemoState.audit` behind the repository; PostgreSQL table `audit_log`, rows only inserted (a reset replaces the whole log). `SCHEMA_VERSION` 5.
- `GET /audit?after_seq=` → AuditEntry[], oldest first (DATA_CONTRACTS §2.2).
- **Done when:** the demo path (mandate v2 → closure → approve the credit line) gives, in order, the mandate entries, the owner's closure, the proposals with their lanes, the move done within the mandate (simulated), the run headline and the owner's approval; every hash matches the documented formula; a changed entry is detected at that entry and a removed one at the entry after it; the AGENT path logs the proposals and the model's headline as `AGENT` with the model name; a reset leaves one entry.

---

## Summary

| Milestone | Features | Result |
|---|---|---|
| A · Cash flow & forecast | B01–B12 | Forecast, closure, backtest via API (no AI) |
| B · Decisions | B13–B19, B33 | Full demo story with backend fallback, activity log |
| C · Persistence | B20–B21 | PostgreSQL, CSV upload |
| D · Agent | B22–B27 | LLM agent through tools, explanations, model health, MCP |
| E · Delivery | B28–B32 | OpenAPI, container, scenario test, logging, extras |

---

# Phase 2: Vero Proof

When a recurring cost changes, Vero finds out why, proves whether the change is justified, and updates everything downstream (payment, cash forecast, letter to the supplier) for the owner's approval. Same company, same `as_of` (5 May 2025) and same bank statement as Phase 1. Every Phase 2 feature traces back to [`PROOF.md`](PROOF.md) (data, rules, demo numbers).

**Rules that hold for every Phase 2 feature**
- **The organizer's CSV is never edited.** Phase 2 adds synthetic documents that agree with it: every invoice total equals a real payment, and every outcome matches what the statement shows afterwards. Phase 1's numbers and tests stay as they are.
- **Uploaded documents are read by the model.** The AI service extracts the line items from the PDF with the LLM; the backend validates every extracted value before using it.
- **Detection is deterministic backend code.** Only a difference with no known reason starts an investigation: **one agent per finding**, at most 6 read-only tool calls, 60 s. The agent proposes a label and a formula; the backend checks the citations, recomputes every number and sets the final label.
- **Reuse Phase 1:** repository boundary, runs and trace, tool registry, orchestrator and fallback, policy lanes, recommendations and receipts, executor, problem details, idempotency.
- **Money** in cents as strings, **percentages** in basis points (8 % = `800`, VAT 19 % = `1900`), **index values** in tenths of a point (121.2 = `1212`). **Unit prices and quantities** are the new `Decimal` type (P17): a decimal string such as `"0.1234"` (euros) or `"2.5"`, never a float.

**Demo numbers (cents)**

| Case | Numbers |
|---|---|
| **Rent, hero** (⚠️ conflict, live upload) | previous 380000, billed 410400 from 1 Jun 2025, variance 30400 (800 bps). Lease §4.2: adjustment only after a ≥ 500 bps VPI move since the last adjustment. VPI 1176 (Jan 2024) → 1212 (Mar 2025) = 306 bps → justified 380000, disputed 30400/month and 364800/year, window impact 91200 (rent on 1 Jun, 1 Jul, 1 Aug) |
| **Fattoria FLV-94818** (✅ verified, digest) | total 89708 (paid 28 Apr). Unit prices differ from the previous invoice FLV-16261 (103721, paid 7 Mar, 2024 prices); the difference is explained by the 2025 price list valid from 1 Apr |
| **Routine checks** (digest, not changes) | Apr payments with a contract behind them match it: rent 380000 (lease), utilities 65000 (payment plan), CHF tranche 416667 CHF (partnership agreement; 433334 EUR @ 1.040, vs Mar 432500 @ 1.038) |
| **Asimex AS-72217** (✅ verified, digest) | total 68723 (paid 26 Apr). Unit prices differ from the previous invoice AS-80497 (58474, paid 8 Mar); explained by Asimex's Q2 price list valid from 1 Apr |
| **Asimex AS-11607** (✅ verified after investigation, digest) | total 62403 (paid 3 May); new line "Logistikzuschlag" 2450 → `unexplained` → the investigation finds Asimex's announcement email (14 Apr) and its terms clause: surcharge 2450 from 1 May on deliveries under 60000 net goods value; AS-11607's goods are below it → `verified`. **Escalated variant** (tests, optional demo switch): the same invoice without the announcement email ends `unexplained` (escalated) |

**Alert rate in the demo:** 4 changes (Fattoria prices, Asimex prices, Asimex surcharge, rent), 3 handled quietly (one after investigating), **1 needs the owner**, plus 3 routine checks.

---

## Milestone F: dataset and Cost Memory

### P01 · Phase 2 design doc — MUST
- `docs/PROOF.md`: the cases, the lease clause, the reference values, the demo numbers above and the German domain notes, written from the team blueprint (v4) so the repo is self-contained.
- **Done when:** every number used in a P-feature's "done when" is in PROOF.md.

### P02 · Dataset fixture — MUST
- One file, `services/api/src/api/fixtures/proof/dataset.json` (synthetic, safe for the public repo, ships in the image):
  - **documents:** id, type (`LEASE`, `CONTRACT`, `INVOICE`, `RENT_ADJUSTMENT`, `NOTICE`, `PRICE_LIST`, `PAYMENT_PLAN`), counterparty, date, language, pages with text blocks and highlight boxes;
  - **line items** (description, quantity and unit price as `Decimal`, VAT rate in bps, net, gross optional), **clauses** (section, text, page, box), **price lists** (unit prices as `Decimal`);
  - the **rent adjustment** as fields, not a line: `old_amount`, `new_amount`, `effective_from`, `frequency: MONTHLY` (its PDF prints both "bisher 3.800,00 €" and "neu 4.104,00 €");
  - **boxes** in the AI service's convention: PDF points, origin top-left (`[x0, y0, x1, y1]`), so the model evaluation compares them directly;
  - **mailbox:** ~20 emails with thread, sender, date, body and attachment ids (the landlord's letter is an attachment in the grease-trap thread; Asimex's surcharge announcement of 14 Apr);
  - **reference values:** Destatis VPI and EUR/CHF, each flagged `demo: true`;
  - every **clause** also carries its terms as structured parameters next to the text, so the backend can check what the agent cites (lease §4.2: `{index: "DESTATIS_VPI", base_period: "2024-01", threshold_bps: 500, last_adjustment: "2024-02-01", min_months_between: 12, written_request: true}`; Asimex terms: `{surcharge: 2450, below_net_goods: 60000, valid_from: "2025-05-01"}`);
  - the line items of every document are also the **expected extraction**: the oracle for P08's model evaluation.
- **Load-time validation** (a broken fixture fails the state load like a broken statement):
  - every invoice total equals a statement payment with the same counterparty, date, amount and invoice number in the reference;
  - the P08 arithmetic holds: net per line = quantity × unit price (half-up), VAT per rate on the totals, gross = net + VAT on the totals;
  - every document is dated before `as_of`; every reference (attachment, clause, price list item) exists;
  - running CHECK over the preloaded invoices **in date order** gives exactly the intended differences: every goods line of a later invoice already appears on the previous one or on the valid price list, so only the price changes and the surcharge stand out.
- **Done when:** a test cross-checks every invoice against the statement (FLV-16261 = 103721, FLV-94818 = 89708, AS-80497 = 58474, AS-72217 = 68723, AS-11607 = 62403) and a broken total fails the load.

### P03 · PDF generator — MUST
- `services/api/scripts/make_proof_pdfs.py`, run with `uv run --with reportlab` (not a runtime dependency): renders every document from P02 into `fixtures/proof/pdf/<doc_id>.pdf` and writes the box of every rendered number and clause back as highlight boxes, so they are exact by construction. reportlab measures from the bottom-left: boxes are converted to the top-left origin before they are stored.
- PDFs have a real text layer (the model reads text, not images). Fixed metadata (creation date, ids) so a re-run gives identical files; the SHA-256 of each PDF is stored in the fixture for P08's fallback.
- It also writes `fixtures/proof/pdf/expected.json` in the AI team's format (`services/ai/tests/test_extract_live.py`: file, `known_counterparties`, and per field the value, printed text, page and box), so their live test runs on the demo PDFs with `EXTRACT_FIXTURES` pointing at that folder. No hand-written expected values.
- **Done when:** a re-run changes no file; the stored box for "4.104,00 €" on the rent adjustment is within 2 points of the box `pypdfium2` reports for that text (top-left origin); `expected.json` lists every uploadable demo document.

### P04 · Cost Memory — MUST
- **CostItem:** counterparty, `cost_ref` (`RENT`, `UTIL`, `XFER`, the other detected fixed costs `PAY`, `SOC`, `WTAX`, or the supplier), expected amount and currency, unit, sources (`STATEMENT`, `CONTRACT`, `PRICE_LIST`, `INVOICE`) with evidence refs. `XFER` is the cost whose Phase 1 outflows are `CHF-…`, so each item also names its outflow prefix.
- Built from Phase 1's recurring detection (B04) on the rows before `as_of`, plus the fixture's documents and price lists. It is **base data** like the imported statement (rebuilt on load and reset); only changeable state is persisted.
- Read API: `GET /api/v1/cost-memory`, `GET /api/v1/documents`, `GET /api/v1/documents/{id}` (metadata, pages, highlight boxes), `GET /api/v1/documents/{id}/file` (the PDF). The mailbox is **not** listed publicly; a document becomes visible when a finding cites it. Each fixture document says where it lives (`source`: `DOCUMENTS`, `MAILBOX`, `UPLOAD`): only the landlord's letter is mailbox-only.
- **Contracts with the feature:** CostItem and Document go into DATA_CONTRACTS §2 and `openapi.json` here, so the frontend can build against them early.
- **Done when:** `RENT` expects 380000 (statement + lease), `XFER` expects 416667 CHF (statement + partnership agreement), Fattoria and Asimex items carry their list prices.

## Milestone G: check and engine

### P05 · Deterministic engine — MUST
- Formula registry `domain/proof/engine.py`: pure functions with an id, an input schema and an `engine_version`:
  - `variance` (delta and bps), `vpi_threshold_indexation`, `fx_conversion` (reuses `money.convert`), `price_list_check`, `surcharge_rule`, `annualize`, `window_impact`.
- `window_impact` runs the Phase 1 forecast engine twice (billed vs. justified amounts) and returns the difference in outflows inside the window and in the lowest balance.
- Percentages round half-up to whole basis points.
- **Done when:** the demo numbers above come out exactly: 306 bps, justified 380000, disputed 30400 / 364800, window 91200, CHF 432500 / 433334; `surcharge_rule` gives 2450 for AS-11607's goods value and 0 for AS-72217.

### P06 · CHECK — MUST
- Input: a document's line items, or the new statement lines of a recurring item. Output: no finding, `verified`, or `unexplained`.
- **Fixed recurring costs:** billed amount vs. the CostItem, ±2 % as in B04 (CHF compares the CHF amount).
- **Supplier invoices:** unit price per line vs. the **previous invoice** from the same supplier (that is the difference); then the price list valid on the invoice date (that is the known reason). A goods line never seen before is explained if it is priced as in the valid price list; a line on no price list (the surcharge) is a difference.
- **Known reasons** (→ `verified` with evidence, no agent): a stored price list that gives exactly the new price. **CHECK never interprets clauses**: choosing and applying a clause is the investigation's job.
- **Routine checks:** each statement payment of a recurring item that has a contract behind it (rent, utilities, CHF tranche) is compared with the contract amount (the CHF tranche in CHF, its EUR amount shown with the row's rate). A match is a routine check record, not a finding; a mismatch is a difference like any other.
- At load and reset, CHECK runs on the preloaded documents **in document date order** (AS-11607 is compared with AS-72217, not AS-80497) and on the statement rows before `as_of`.
- **Done when:** rent adjustment → `unexplained` (30400, 800 bps); FLV-94818 vs FLV-16261 and AS-72217 vs AS-80497 → `verified` (price lists); AS-11607 → `unexplained` (new line); the Apr rent, utilities and CHF tranche → routine checks passed, no findings.

### P07 · Findings — MUST
- **Finding** as in the blueprint's schema: classification and history, status, counterparty, `cost_ref`, billing period, source document, line item, trace (via `run_id`), financial impact, evidence refs, propagation, recommendation ids.
- **Status:** `INGESTED → EXTRACTED → CHECKED → (INVESTIGATING) → CLASSIFIED → PENDING_APPROVAL → RESOLVED`; every transition is a trace step.
- Persisted behind the repository (in memory and PostgreSQL), table `proof_findings`, `SCHEMA_VERSION` 6 (5 is the activity log, B33). Routine checks are base data, rebuilt with Cost Memory on every load and reset. Proof runs, findings and their decisions also write activity log entries with `agent: PROOF`.
- `GET /api/v1/findings?classification=&status=`, `GET /api/v1/findings/{id}`, `POST /api/v1/findings/{id}/acknowledge` (`Idempotency-Key`; the owner's one tap on a quiet finding: it leaves the digest; `NOT_PENDING` if already acknowledged or not quiet). Findings with actions leave the digest when their actions are decided (P14).
- **Schema name `ProofFinding`:** `Finding` is already Phase 1's ANALYSED fact (`schemas.py`, DATA_CONTRACTS §2.2), and OpenAPI component names must be unique. The path stays `/findings`. Written into DATA_CONTRACTS §2 and `openapi.json` with this feature.
- **Done when:** findings round-trip in both repositories, a reset recreates the preloaded findings identically, and acknowledging the Fattoria finding removes it from the digest.

### P08 · Document upload and LLM extraction — MUST
- `POST /api/v1/documents` (multipart PDF, at most 5 MB, `Idempotency-Key`; `fastapi[standard]` already brings multipart support). The backend stores the file and starts a **Proof run** (P10): extracting → checking → investigating (only if needed) → done, each phase a trace step, so the UI can show progress.
- **While another run is in progress** (either kind) → 409 `RUN_IN_PROGRESS`, checked before anything is stored, as for Phase 1 events.
- **Storage:** the PDF bytes live behind the repository (PostgreSQL `bytea` in its own table; in memory for the in-memory repository). The API is a single replica with no volume, so not on disk. Reset deletes uploads.
- Returns `202 {document_id, run_id}`; the findings appear on the run and the document. The same file (SHA-256) or the same counterparty and document number again → `DOCUMENT_ALREADY_PROCESSED` (409).
- **Extraction by the AI service** (agreed with the AI team; full shapes in DATA_CONTRACTS §3): `POST ai/v1/documents/extract {document_id, filename, content_base64, known_counterparties: [{id, name, aliases[]}]}`, `INTERNAL_TOKEN`, answered within 30 s.
  - The AI service reads the text layer with `pypdfium2` (already a dependency). The model never writes numbers or boxes: it returns, per field, the printed text and the line it is on; code finds that text, takes its box from the PDF and parses the value (`1.234,56` → `"123456"`, `01.06.2025` → `"2025-06-01"`). Schema-constrained JSON, no tools, document text passed as data.
  - The AI service runs the arithmetic checks itself and gives the model one correction round, inside the 30 s.
  - **Response:** `{document_id, status: OK | FAILED, errors[], pages[{page, width, height}], doc_type, counterparty, document_number, date, period{from, to}, effective_from, currency, lines[], totals{net, vat, gross}}`; a rent adjustment adds `old_amount`, `new_amount`, `frequency`. Every value is `{value, source: {page, text, box}}`; absent fields are null. The counterparty also carries `matched_id` (one of the `known_counterparties`, or null).
  - **Unreadable input** → 422 `{code, message}` (`NO_TEXT_LAYER` and the others defined in `services/ai/src/ai/extraction.py`; limits there: 15 MB, 20 pages). **Model trouble or failed checks** → 200 with `status: FAILED` and `errors`.
  - Implemented on the AI side (PR #23); the request and response go into DATA_CONTRACTS §3 with this feature.
- **The backend validates every value anyway** (its checks are the authority):
  - each value's `source.text` parses (existing German parsers) to the same value;
  - net per line = quantity × unit price, rounded half-up to the cent;
  - VAT per rate on the totals = the rate × that rate's net, ±1 cent per rate (German invoices round VAT on the totals); gross = net + VAT exactly on the totals; gross per line is optional;
  - `matched_id` is accepted only if the printed counterparty contains that party's name or an alias after normalising; no match → the document is stored with no check and the trace says so;
  - the document date is not after `as_of`.
- **When extraction fails** (AI unreachable, timeout, a 422, `status: FAILED`, invalid shape, a failed backend check): a document whose SHA-256 is in the fixture gets its stored values and the run is marked `FALLBACK` with the reason; any other document ends `EXTRACTION_FAILED` with what failed, and no finding. The stage demo must use the live extraction (hard criterion); the fallback is a visible safety net.
- **Rent adjustments:** CHECK takes `new_amount` from `effective_from` as the billed amount for `RENT`.
- **Preloaded documents** (load and reset) use their fixture values, with no model call: reset stays fast and deterministic.
- **Model evaluation** (AI team's `test_extract_live.py`, skipped unless `LLM_BASE_URL` is set, plus offline tests with a scripted model): live extraction of every fixture PDF equals its fixture fields, lines, totals and boxes.
- **As built:** the Proof run stops after CHECK until P10 adds the investigation; an UNEXPLAINED finding stays `CHECKED` with "the investigation looks for the reason next" in the trace. The same counterparty and document number found after extraction ends the run `FAILED` (upload status `DUPLICATE`): only the SHA-256 can be checked before storing. `SCHEMA_VERSION` 7 (tables `uploads`, `document_files`; runs gain `kind`, `document_id`, `finding_ids`).
- **Done when**, with a fake AI service: a correct extraction of `doc_rent_adjustment_2025_06` (`new_amount` 410400 from 2025-06-01) creates the rent finding and starts the investigation; a value whose source text parses differently, VAT off by 2 cents on one rate, or a `matched_id` the printed name doesn't support is rejected → stored values and `FALLBACK` for the rent adjustment, `EXTRACTION_FAILED` for an unknown PDF; a 422 `NO_TEXT_LAYER` → `EXTRACTION_FAILED`; the same upload twice → 409.

## Milestone H: investigation

### P09 · Investigation tools — MUST
- Same registry and route as B22, with tools **scoped by run kind**: a Proof run can't call Cash tools and the reverse (`UNKNOWN_TOOL`). Limit per investigation: `INVESTIGATION_MAX_TOOL_CALLS=6`.
- `GET /internal/v1/tools` today lists every tool with no run (`routers/internal.py`), and the AI loop offers all of them. It becomes `GET /tools?run_id=…`, returning only that run's kind (without `run_id`: the Cash tools, so the current AI service keeps working).
- Tools (read-only, deterministic over the fixture):
  - `search_inbox(query, counterparty, date_range)`, `search_documents(query, doc_type)`: keyword search with a fixed ranking on the **same code as the RAG assistant** (`api/rag.py`). Today `retrieve()` builds its records from the Phase 1 state and scores them inline; P09 first splits out the term handling (`_terms`, stop words, aliases) and a generic `rank(query, items, text_of)`, which `retrieve()` and the new tools both call. The existing RAG tests must still pass. German aliases mapped to the same English terms on both sides (*miete*, *mieterhöhung*, *mietanpassung* → rent; *nebenkosten*; *logistikzuschlag* → surcharge), so an English query finds German emails;
  - `read_clause(doc_id, section)`: text, page and box;
  - `get_reference_index(name, periods)`: VPI or EUR/CHF, flagged as demo values;
  - `get_price_list(supplier, item, date)`;
  - `compute(formula_id, inputs)`: P05.
- Every result carries the evidence ids it returns; every call is a trace step with a plain label ("Searching the inbox for letters from Main Street Real Estate").
- **Email and document text is untrusted data**, as bank references are for the RAG assistant: tool results are returned as data, the investigation prompt says so, and nothing in them can change a label (P11 sets it).
- **Done when:** the hero's 4-step sequence returns the expected results, the hero query ranks the landlord's letter first among the ~20 emails, and a 7th call returns `TOOL_LIMIT_REACHED`.

### P10 · Proof runs and investigations — MUST
- Runs get a `kind` (`CASH`, `PROOF`), a `document_id` and the finding ids; new trigger `DOCUMENT_UPLOADED`. A Proof run covers the whole document: extraction (P08), CHECK, and the investigation of each `unexplained` finding, one after another.
- **After a restart** (`AgentOrchestrator.recover()` falls back every run left `RUNNING`): a Proof run interrupted during extraction uses the fixture values for a demo document or ends `EXTRACTION_FAILED`; one interrupted during an investigation finishes with the scripted investigation (P12). Reason `SERVICE_RESTARTED`.
- An investigation starts through the AI service (`POST ai/v1/agent/runs` with the kind and the finding's facts; contract to agree with the AI team). One run at a time across both kinds (`RUN_IN_PROGRESS`), 60 s deadline per investigation, the same fallback reasons as Phase 1.
- `/complete` for an investigation: `{classification, compute_call_id, evidence_refs[], explanation, letter_draft?, model, prompt_version, tool_calls}`; wrong shape → 422.
- **AI service side** (Ege): a second loop configuration for investigations, with its own prompt and its own final tool `submit_finding` next to the existing `submit_proposals`; plus the extraction endpoint (P08).
- **Done when:** with a fake AI service: the AGENT path, a timeout → FALLBACK, `/fail` → FALLBACK and a late callback → `RUN_CLOSED`.
- **As built:** after CHECK an `UNEXPLAINED` finding goes `INVESTIGATING` and the run stays `RUNNING` (`tool_log.investigation`), then `ProofPipeline.investigate` sends AgentRunStart with `kind: PROOF`, the top-level `finding` (where the AI service's loop reads it) and `investigation { cost_item, hypotheses }` and arms the 60 s deadline. Callbacks are routed by run kind; `/complete` takes InvestigationCompletion (DATA_CONTRACTS §3.3). Every ending goes through `proof_runs.conclude_investigation`, the seam for P11 (the agent's answer, stored on the run as `tool_log.completion`; the finding stays `UNEXPLAINED` until then) and P12 (without the agent: `UNEXPLAINED`, `escalated`, run `FALLBACK`). Reasons: `AGENT_DISABLED`, `AI_UNREACHABLE`, `DEADLINE_PASSED`, `DATA_CHANGED`, `SERVICE_RESTARTED`, `INVALID_ANSWER`, or the agent's own. `INVESTIGATION_AGENT_ENABLED` defaulted to `false` until the AI service's investigation loop existed (PR #40); it is now `true` and stays as an off switch. Cash start payloads gain `kind: CASH`. No schema change.
- **Agent guidance (22 Nov, after live runs where the model spent its 6 look-ups without computing):** `read_clause` on a document without sections (a notice, a rent adjustment) answers `INVALID_ARGUMENT` saying its full text was in the search result and listing the counterparty's documents that have sections, and a successful read gets the trace result "Found §4.2 Wertsicherung, page 1". `hypotheses` are plain sentences instead of codes (`proof_runs.HYPOTHESES`; still `string[]`): the clause to look for is the one that allows the amount to change, not the one stating it. Measured locally with the live model and the unchanged prompt: 3 of 3 rent uploads still ended in the scripted investigation (same CONFLICT), but the agent no longer retried the notice and reached §4.2 once; the rest is the prompt's (sent to the AI team).
- **Index months in grounding (22 Nov, with the AI team's prompt `investigation-3`, #58):** the agent now reads §4.2 on its third call, but when the month it asks for has no value it computed with the base value as the current one (1176 → 1176, "0.00 %") and the old check accepted it, because 1176 came from a tool result. `Rule.check` (`_index_months` for `vpi_threshold_indexation`): `base_index` must be the value of the read clause's `base_period`, `current_index` the series' latest value up to `as_of` (the month the scripted H3 uses); otherwise "Can't use the answer: current_index '1176' is not the DESTATIS_VPI value for 2025-03, the latest published up to 05 May 2025" and the scripted investigation takes over. Live, 5 rent uploads: 5 × correct CONFLICT in 35–49 s, all FALLBACK (3 rejected by this check, 2 spent their look-ups on months without a value).

### P11 · Grounding check and final label — MUST
- A citation that no tool returned in this run is dropped and noted in the trace.
- The backend recomputes the chosen formula from the values the tools returned; numbers in the completion are ignored.
- **The `compute` call is the authority.** The completion names it by `compute_call_id`. Every input of that call must come from Cost Memory or from an earlier tool result in this run: amounts from Cost Memory or the billing document, index values from `get_reference_index`, clause terms from the structured parameters `read_clause` returned. An input with no source makes the result unusable (→ escalated, noted in the trace).
- The AI service applies the same checks as its RAG answers before calling `/complete` (only evidence ids returned by this run's tools, no number absent from the cited results). The backend's check is the one that decides.
- **Final label:**
  - billed above the justified amount → `conflict`;
  - billed equal to the justified amount → `verified`;
  - no usable evidence left → `unexplained` (escalated), with the list of what was searched.
- **Done when:** a completion citing a document no tool returned loses that citation; a completion claiming `verified` for the rent ends as `conflict` with the engine's numbers; a `compute` call with a threshold of 1000 bps (not what §4.2 says) is rejected.
- **As built:** `domain/proof/grounding.py` (`ground`, `RULES`, `base_sources`, `calls_of`, `evidence_ref`), called from `proof_runs.conclude_investigation`. Each stored tool call now keeps `seq`, `tool`, `arguments` and its plain `label`. Label formulas: `vpi_threshold_indexation` and `surcharge_rule` (others → "does not decide a label"). Inputs are checked by kind of source (DATA_CONTRACTS §3.3); the base amount and the net goods must be what the cost was before the change, never the billed amount, so a model can't make a change "verified" by indexing the new rent. The finding gains `investigation` (recomputed numbers, `disputed`, `searched`, `dropped_refs`, `problems`, the agent's `note` and `proposed` label) and evidence with `ref` (new kind `REFERENCE_VALUE`). Trace steps `GROUND`. Headlines: "…: 304,00 € more than allowed", "…: verified", "…: needs your review". No schema version change (findings are JSON bodies).

### P12 · Scripted investigation (FALLBACK) — MUST
- A fixed hypothesis order per cost type (H1 notice, H2 clause, H3 reference or price list, H4 quantity) calling the same tools, with `source: BACKEND` trace steps.
- Used when the agent fails, and at load and reset for preloaded `unexplained` findings (the Asimex surcharge).
- **Done when:** the FALLBACK hero gives the same label and numbers as the AGENT path; the Asimex surcharge ends `verified` (announcement email + terms clause, `surcharge_rule` 2450); without the announcement email it ends `unexplained` (escalated) with its search list.
- **As built:** `api/scripted_investigation.py`: `investigate(state, finding)` runs the hypotheses through the same tool handlers (`PROOF_TOOLS`), then `ground(..., mode=FALLBACK)` with the calls it made, so both paths share P11's check and `Grounding.apply`. **H1** `search_inbox` (query "rent increase" for a recurring amount, the charge's description for a new charge; counterparty id); the top email is cited only if it matches every query term. **H2** for a recurring amount: the clauses with `index`, `base_period`, `threshold_bps` in its Cost Memory contracts; for a new charge: the clauses with `surcharge`, `below_net_goods`, `valid_from` in **the documents the announcement brought only** (a new charge needs an announcement; otherwise `terms_asimex_2025`, which Cost Memory also lists, would verify it without the email). **H3** `get_reference_index` (base period + latest value up to `as_of`) and `compute`. **H4** (quantity) has no scripted step: escalated. Used by `conclude_investigation` for every ending without the agent (trace: "…: Vero follows its own steps", then `INVESTIGATE`/`TOOL_CALL`/`TOOL_RESULT`/`GROUND` steps, all `BACKEND`), and by `build_base` → `investigate_preloaded` at load and reset (load time, no run, ~0.03 s). The tool modules import `DemoState` for typing only, so `state.py` can import the script. Preloaded findings are now 3 × VERIFIED and quiet (PROOF §7.2).

## Milestone I: propagate and act

- **Changed on 22 Nov (cluster evaluation run):** an agent answer the grounding check can't use no longer ends the finding escalated. The agent computed the hero's numbers, then submitted `UNEXPLAINED` (which carries no `compute_call_id`), and the backend kept `UNEXPLAINED` instead of the conflict the evidence proves. Now `conclude_investigation` runs the scripted investigation for any ungroundable answer (reason `AGENT_INCONCLUSIVE`, trace "The agent found no reason the evidence supports: Vero follows its own steps", mode `FALLBACK`); the `GROUND` step still says why the answer was rejected. A finding is escalated only when Vero's own steps find nothing either. Tests: `test_grounding.py` (every rejection case now also ends in the hero's conflict; `test_an_agent_that_labels_the_conflict_unexplained_is_overruled`).
- **Agent loop, same night (applied on the AI team's behalf, with the user's clearance):** once its 6 calls are spent, the investigation agent gets one turn to submit a valid finding; anything else (a rejected `submit_finding`, another tool call, plain text) fails at once with `TOOL_LIMIT_REACHED`, so the backend's scripted investigation takes over. Before, it circled between "call compute first" and `TOOL_LIMIT_REACHED` until the 60 s deadline (73 s uploads on the cluster). Trace: "The agent used all its look-ups without an answer: Vero follows its own steps". Tests: `services/ai/tests/test_investigation.py` (three replays of the loop, one valid answer on the last call); checked end to end against the backend (all three cluster behaviours end in the hero's conflict in under 0.1 s).

### P13 · Propagation — MUST
- Per finding: the payment decision (keep, or the new amount), the forecast effect (P05 `window_impact`) and the disputed amounts.
- A `verified` change with a new amount proposes an update of the recurring item's future outflows.
- **Done when:** hero → keep `RENT` at 380000, window impact 91200, S0 unchanged; a variant with the VPI above the threshold proposes the indexed amount for `RENT-06…08`.
- **As built:** `domain/proof/propagation.py` (`propagate(finding, cost_item, forecast)`, `describe`); the shapes (`FindingPropagation`, `PaymentDecision`, `ForecastEffect`) live in `findings.py` and replace the untyped `propagation`. Numbers only from the engine (`window_impact` twice: billed vs proposed for `disputed_in_window`, current vs proposed for `forecast`; `annualize`). Set wherever a finding is classified: `proof_runs.with_propagation` after an investigation (trace step `PROPAGATE`) and after a CHECK that verifies an upload, and in `build_base` for the preloaded findings (invoices → `PAID`). Only a `CONFLICT` has disputed amounts. Nothing changes the schedule; `UPDATE` waits for P14's `execute_amount_change`. Variant numbers (PROOF §7.5): `UPDATE` 399388, disputed 11012 / 132144 / 33036, forecast +58164.

### P14 · Actions, policy and execution — MUST
- New recommendation kinds `KEEP_PAYMENT`, `UPDATE_RECURRING_AMOUNT`, `SEND_LETTER`, linked to their finding.
- **Policy**, through the same checks and lanes as B16:
  - amount changes on protected categories (`PAYROLL`, `SOCIAL_SECURITY`, `TAX`) → `NOT_ALLOWED`;
  - letters and amount changes → always `NEEDS_APPROVAL` (they contact a third party or move money);
  - pause respected.
- **Executor:** a new `execute_amount_change` in `decisions.py`, next to `execute_move` / `execute_credit` (still the only code that changes the schedule); it bumps `financial_version`. Letters are marked sent with a `SIM-…` confirmation.
- **The outflow keeps its original amount** (as it keeps `due_date` next to `scheduled_date`). The backtest restores it as it restores dates (`state.backtest()`), so simulated amount changes never change the backtest numbers.
- An executed amount change bumps `financial_version`, so a pending Phase 1 recommendation becomes `STALE_RECOMMENDATION`, which is correct: the gap changed.
- Approve and reject through the existing `/recommendations/{id}/approve` and `/reject`, with receipts. The finding is `RESOLVED` once all its actions are decided.
- **Done when:** approving the hero's actions resolves the finding with receipts and leaves the forecast unchanged; the VPI-above-threshold variant changes `RENT-06…08` and the forecast, and the backtest numbers stay as in B08.
- **As built:** `api/proof_actions.py` (`propose_actions`, `letter_subject`), called at the end of `conclude_investigation` from the finding's `propagation`; `domain/policy.py: evaluate_proof_action` (R4, R6, R7, pause); `decisions.py: execute_amount_change` and `settle_finding`; `decide()` handles the new kinds (audit agent `PROOF`, no Phase 1 refresh of pending items). `Recommendation` gains `finding_id`, `outflow_ids`, `outflow_versions`, `amount`; `Outflow` gains `base_amount` (restored by `state.backtest()`). A new Cash run supersedes only Cash proposals; a Proof action is stale only if one of its payments changed. Storage: `SCHEMA_VERSION` 8 (`recommendations` + `finding_id`, `outflow_ids`, `outflow_versions`, `amount`; `outflow_changes` + `amount`). Public: three new subjects, `Recommendation.finding_id`, `Reason.finding_id`/`disputed`, rules R6/R7. The letter's text is the agent's draft until P16.

### P15 · Weekly digest and overview — MUST
- `GET /api/v1/digest`: the findings the owner hasn't acknowledged (P07) or decided (P14) yet and the latest routine checks, with **no date window** (the demo clock is fixed at 5 May; "weekly" is only the label). Counts: changes handled quietly, changes needing the owner, routine checks passed. One row each with label, short reason and staged actions.
- `GET /overview` gets a Proof block: open findings by label and the latest Proof run.
- Digest goes into DATA_CONTRACTS §2 and `openapi.json` with this feature.
- **Done when:** after reset the digest shows 3 changes handled quietly (Fattoria prices, Asimex prices, Asimex surcharge "after investigating"), 0 needing the owner and 3 routine checks passed (rent, utilities, CHF tranche); after the hero upload: 4 changes, 3 handled quietly, **1 needing the owner**, 3 routine checks.
- **As built:** `api/digest.py` (`build_digest`, `latest_routine_checks`, `by_period`, also used by `GET /findings`), `GET /digest` in `routers/proof.py`, shapes `Digest`, `DigestCounts`, `DigestChange`, `DigestAction` in `proof_schemas.py` (routine checks reuse `RoutineCheck`). Changes = findings with `in_digest`; quiet = `finding.quiet`; needing the owner = the rest (pending actions, an escalated `UNEXPLAINED`). Routine checks = the latest of the 13 stored checks per cost item, in Cost Memory order: RENT 1 May (the latest rent before `as_of`, not April), UTIL 10 Apr, XFER 15 Apr (in CHF); counted when they match. Each row: `reason` = the history label that set the classification, `investigation_mode` ("after investigating"), `actions` = the finding's `PENDING` recommendations with `subject_text`. `GET /overview` gains `proof: { open_findings: { VERIFIED, UNEXPLAINED, CONFLICT }, needs_owner, latest_run }`; the top-level `latest_run` and `headline` now come from **Cash runs only** (before, a Proof upload replaced the Cash headline with its own summary). No storage change. Tests: `test_digest.py` (both §7.4 moments, acknowledge and decide, reset, the escalated variant, the Cash overview).

### P16 · Letter text — SHOULD
- The German clarification letter: the agent's `letter_draft` when the run succeeded, else a backend template citing the clause, the justified amount and a request for the basis of the change. Neutral wording, never a refusal to pay.
- **Done when:** with the AI down, the hero letter comes from the template with `source: TEMPLATE`.
- **As built:** `api/letters.py`: `write_letter(state, finding, category, draft) -> Letter(text, source, rejected)`, `template_letter`, `check_draft`, `euro_amounts`, `cited_clause` (now also used by `proof_actions._clause`). Called by `propose_actions` for `SEND_LETTER`, which fills `subject.draft` (always) and the new `subject.draft_source` (`MODEL` | `TEMPLATE`; named `draft_source`, not `source`, next to the card's `explanation.source`). **The agent's draft is checked like any model output** (the spec only said "when the run succeeded"): used only if the agent proposed `CONFLICT` itself, every euro amount in it is one of the finding's engine amounts, it states the justified amount, cites the clause section, and contains no refusal or threat wording; otherwise the template, with a `GROUND` trace step "The agent's letter is not used: …". The template is German, from the engine's numbers: the change (document date, old → new amount, start), the clause's rule (for `vpi_threshold_indexation`: threshold, VPI base and current with their months, change in %, and either "no adjustment, the rent stays 3.800,00 €" or the indexed amount), a request for the basis and the calculation, the owner's name. It does not say what Vero will pay, so it stays true whatever the owner decides on `KEEP_PAYMENT`. Amounts in letters use German grouping (`3.800,00 €`). No storage change. Tests: `test_letters.py` (AI down → template, the exact hero text; an accepted draft; four rejected drafts; an overruled label; the VPI variant's 3.993,88 €; amount parsing).

## Milestone J: delivery

### P17 · Contracts and OpenAPI — MUST
- The Phase 2 shapes are written **with the feature that creates them** (CostItem and Document with P04, `ProofFinding` with P07, extraction with P08, Digest with P15), so the frontend never waits for the end. P17 is what remains and the final check: the new **`Decimal`** type in §0 (a string matching `^-?[0-9]{1,9}(\.[0-9]{1,6})?$`, no floats, no thousands separators; used for unit prices and quantities), the new recommendation kinds, the investigation start/complete payloads (§3), the new error codes, and ARCHITECTURE's Proof run sequence. `make schema`.
- **RAG assistant contract** (`POST /api/v1/assistant/ask`, merged): align its sources with §0: `amount_cents` → `amount` (the `Money` type already means cents), `kind` values in `UPPER_SNAKE` (`TRANSACTION`, `OUTFLOW`, `INFLOW`), and `sources` typed (today `dict[str, str]`, untyped in OpenAPI). Document the endpoint in DATA_CONTRACTS §2. When the AI service is down it already answers with the matching records (`answer_source: "TEMPLATE"`) instead of an error, so no new error code is needed. Agree the renames with the AI team (the browser reads these fields).
- **Done when:** `test_openapi.py` passes and the AI team has agreed the investigation payloads and the RAG renames.
- **As built:** most of P17 had been written feature by feature (the `Decimal` type in §0, the Proof recommendation kinds, the investigation payloads in §3, every `ErrorCode` in the Problem table); a diff of `openapi.json` and the code against DATA_CONTRACTS found the rest. **RAG (user's choice: public API only):** `POST /assistant/ask` now answers with typed `AssistantSource` (`kind` `TRANSACTION`/`OUTFLOW`/`INFLOW`, `date`, `amount` as `Money`); the backend ↔ AI payload (`POST /v1/rag/answer`, `RagRecord` with `amount_cents` and lower-case kinds) is unchanged, so the AI service needs no change, and the backend keeps only cited ids it retrieved (an unknown id is dropped; an unusable answer lists the records with `answer_source: TEMPLATE`). Contracts: §2.1 rows for `POST /assistant/ask` and `GET /company`, §2.2 `AskResponse`/`AssistantSource`, §3.1 the RAG call (the AI → backend rows renumbered 7–11), `TraceStep.kind` with `DISCARDED`, `INVESTIGATE`, `GROUND`, `PROPAGATE`. `ARCHITECTURE.md` §3: the Proof run (upload → extract → CHECK → investigation → GROUND → PROPAGATE → POLICY), the investigation loop, the scripted fallback, deciding the actions with the digest, the finding state machine. The investigation payloads count as agreed: the AI team built against them (#40, #42).

### P18 · Demo reset for both phases — MUST
- Reset rebuilds Cost Memory, deletes uploads, findings and Proof runs, and recreates the preloaded findings (P06, P12) without any model call. Still under 5 s on PostgreSQL.
- **Done when:** two resets give identical findings and digest, and the Phase 1 S0 numbers are unchanged.
- **As built:** no code change was needed: `repository.reset()` already rebuilt the base (`build_base`: statement, Cost Memory, CHECK, the scripted investigation of AS-11607, propagation) and `fresh_state` saved over everything, and `_save` deletes the runs, recommendations, receipts, uploads, `document_files`, idempotency keys and audit rows the fresh state doesn't have. Every background path (extraction, deadline timer, agent callbacks, tool calls) checks that its run still exists and is running, so a reset during an investigation ends it quietly (`RUN_CLOSED`). Proved by `tests/test_reset.py` on both stores: a snapshot of every public GET (except health) is identical after one and two resets following a busy demo (hero decided, a quiet change acknowledged, mandate v2, closure run, credit approved), S0 unchanged, digest 3 / 3 / 0 / 3; the upload and its file are gone and can be uploaded again; an executed amount change (VPI variant) is undone; a reset in the middle of an investigation; the reset under 5 s (measured on PostgreSQL: 0.08–0.25 s).

### P19 · Scenario test — MUST (release gate)
- Through the public API: reset → digest (3 quiet, 0 needing the owner, 3 routine checks) → upload the rent adjustment → Proof run `SUCCEEDED` (extraction, check, investigation) → finding `conflict` with the demo numbers → digest (4 changes, 3 quiet, 1 needing the owner) → approve → `RESOLVED`, receipts → digest (3 changes, 3 quiet, 0 needing the owner: a decided finding leaves it, PROOF §6) → reset. The Phase 1 scenario (B30) still passes after it.
- **Demo order is fixed and tested:** Proof first (the hero keeps the rent, nothing changes), then the Phase 1 closure story. A second test runs the VPI-above-threshold variant after the closure run and checks that the pending credit line turns `STALE_RECOMMENDATION`.
- Must pass in FALLBACK and AGENT (fake AI) modes.
- **As built:** `tests/test_proof_scenario.py`, both tests in FALLBACK and AGENT mode (`ScenarioAI`: extracts the demo PDFs like the model and accepts agent runs; the test calls the Proof tools and `complete` like the model, PROOF §7.1's four calls). **Demo order:** reset → digest 3/3/0/3 (Fattoria, Asimex, the Asimex surcharge "after investigating") → upload → run `SUCCEEDED` (trace `RECEIVE` … `CHECK`, `INVESTIGATE`, `GROUND`, `PROPAGATE`, `POLICY`, `SUMMARY`; `EXTRACT` with the model) → `CONFLICT` 410400 vs 380000, 306 bps, disputed 30400 / 364800 / 91200, `KEEP_PAYMENT` + `SEND_LETTER` (template) → digest 4/3/1/3 with the staged actions → approve both, `PROPOSAL` + `DECISION` receipts → `RESOLVED` → digest 3/3/0/3, S0 unchanged → B30's closure story with DESIGN.md's numbers (S2, CHF-06, 20 000 € credit) on the same state → audit chain holds → reset gives the fresh state again. **Variant (§7.5) after the closure run:** the credit line stays `PENDING` through the upload; approving `UPDATE_RECURRING_AMOUNT` 380000 → 399388 bumps `financial_version` and changes RENT-06…08, and the credit line's approval gets `409 STALE_RECOMMENDATION` (status `STALE`); the letter stays pending. B30's closure part became `test_scenario.closure_story(client, ai, agent, approve_credit=True)`, shared by both release gates. The P19 line above was corrected (the digest drops to 0 needing the owner after the decision; user's decision after P15).

### P20 · Scanned PDFs — MIGHT
- PDFs without a text layer (photos, scans) end `EXTRACTION_FAILED` (`NO_TEXT_LAYER`). The AI service already has OCR (RapidOCR, `/v1/documents/text`), so supporting them means extracting from OCR lines and their boxes instead of the text layer; the same backend checks apply.

### P21 · Re-check on contract upload — MIGHT
- Uploading a lease or contract updates Cost Memory and re-runs CHECK on the payments it covers.
- **Not built (user's decision, 21 Nov 2026).** The demo's lease and contracts are preloaded (re-uploading one gives `409 DOCUMENT_ALREADY_PROCESSED`), so the feature would need a new demo document plus contract fields in the AI extraction. A contract upload today is read, finds no billing values and ends "no change". The frontend hides or drops its Contract check page in live mode.

### P22 · MCP for the investigation tools — MIGHT
- With B26: the investigation tools on `/internal/mcp`.

### P23 · Findings and documents as RAG sources — MIGHT
- The RAG assistant also retrieves findings and the documents they cite, so the owner can ask "Why is the rent still 3 800?" and get an answer citing the finding and lease §4.2. The computed numbers (30400, 364800) are in the finding, so the RAG number check still passes. Only documents already cited by a finding are retrievable; the mailbox stays private.
- **Done when:** that question returns the rent finding and the lease as sources.

---

## Summary (Phase 2)

| Milestone | Features | Result |
|---|---|---|
| F · Dataset & Cost Memory | P01–P04 | Design doc, fixture validated against the statement, PDFs with exact highlights, Cost Memory API |
| G · Check & engine | P05–P08 | Formulas, deterministic CHECK, findings, live upload with LLM extraction |
| H · Investigation | P09–P12 | Tools, Proof runs, grounding check, fallback |
| I · Propagate & act | P13–P16 | Payment decision, forecast impact, approvals, digest, letter |
| J · Delivery | P17–P23 | Contracts, RAG alignment, reset, scenario test, extras |
