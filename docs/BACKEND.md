# Backend (`services/api`): features to implement

Phase 1, Vero Cash (B01–B32), and below it [Phase 2, Vero Proof](#phase-2-vero-proof) (P01–P22). Every Phase 1 feature traces back to [`DESIGN.md`](DESIGN.md) (rules, scenario numbers), [`contracts/DATA_CONTRACTS.md`](contracts/DATA_CONTRACTS.md) (fields, endpoints) and [`ARCHITECTURE.md`](ARCHITECTURE.md) (sequences).

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

---

# Phase 2: Vero Proof

When a recurring cost changes, Vero finds out why, proves whether the change is justified, and updates everything downstream (payment, cash forecast, letter to the supplier) for the owner's approval. Same company, same `as_of` (5 May 2025) and same bank statement as Phase 1.

**Rules that hold for every Phase 2 feature**
- **The organizer's CSV is never edited.** Phase 2 adds synthetic documents that agree with it: every invoice total equals a real payment, and every outcome matches what the statement shows afterwards. Phase 1's numbers and tests stay as they are.
- **Detection is deterministic backend code.** Only a difference with no known reason starts an investigation: **one agent per finding**, at most 6 read-only tool calls, 60 s. The agent proposes a label and a formula; the backend checks the citations, recomputes every number and sets the final label.
- **Reuse Phase 1:** repository boundary, runs and trace, tool registry, orchestrator and fallback, policy lanes, recommendations and receipts, executor, problem details, idempotency.
- **Money** in cents as strings, **percentages** in basis points (8 % = `800`), **index values** in tenths of a point (121.2 = `1212`).

**Demo numbers (cents)**

| Case | Numbers |
|---|---|
| **Rent, hero** (⚠️ conflict, live upload) | previous 380000, billed 410400 from 1 Jun 2025, variance 30400 (800 bps). Lease §4.2: adjustment only after a ≥ 500 bps VPI move since the last adjustment. VPI 1176 (Jan 2024) → 1212 (Mar 2025) = 306 bps → justified 380000, disputed 30400/month and 364800/year, window impact 91200 (rent on 1 Jun, 1 Jul, 1 Aug) |
| **CHF tranche** (✅ verified, digest) | 416667 CHF every month; Mar 432500 EUR @ 1.038, Apr 433334 EUR @ 1.040; variance 834 explained by the rate |
| **Fattoria FLV-94818** (✅ verified, digest) | total 89708 (paid 28 Apr); unit prices equal the 2025 price list |
| **Asimex AS-11607** (🔍 escalated, digest) | total 62403 (paid 3 May); new line "Logistikzuschlag" 2450, no evidence anywhere; exposure 29400/month at ≈ 12 deliveries/month (assumption shown) |

---

## Milestone F: dataset and Cost Memory

### P01 · Phase 2 design doc — MUST
- `docs/PROOF.md`: the cases, the lease clause, the reference values, the demo numbers above and the German domain notes, written from the team blueprint (v4) so the repo is self-contained.
- **Done when:** every number used in a P-feature's "done when" is in PROOF.md.

### P02 · Dataset fixture — MUST
- One file, `services/api/src/api/fixtures/proof/dataset.json` (synthetic, safe for the public repo, ships in the image):
  - **documents:** id, type (`LEASE`, `CONTRACT`, `INVOICE`, `RENT_ADJUSTMENT`, `NOTICE`, `PRICE_LIST`, `PAYMENT_PLAN`), counterparty, date, language, pages with text blocks and highlight boxes;
  - **line items** (description, quantity, unit price, VAT rate, net, gross), **clauses** (section, text, page, box), **price lists**;
  - **mailbox:** ~20 emails with thread, sender, date, body and attachment ids (the landlord's letter is an attachment in the grease-trap thread);
  - **reference values:** Destatis VPI and EUR/CHF, each flagged `demo: true`.
- **Load-time validation** (a broken fixture fails the state load like a broken statement):
  - every invoice total equals a statement payment with the same counterparty, date, amount and invoice number in the reference;
  - net + VAT (7 % / 19 %, half-up) adds up per line and per invoice;
  - every document is dated before `as_of`; every reference (attachment, clause, price list item) exists.
- **Done when:** a test cross-checks every invoice against the statement (FLV-59323 = 97813, FLV-94818 = 89708, AS-72217 = 68723, AS-11607 = 62403) and a broken total fails the load.

### P03 · PDF generator — MUST
- `services/api/scripts/make_proof_pdfs.py`, run with `uv run --with reportlab` (not a runtime dependency): renders every document from P02 into `fixtures/proof/pdf/<doc_id>.pdf` and writes the box of every rendered number and clause back as highlight boxes, so they are exact by construction.
- Fixed metadata (creation date, ids) so a re-run gives identical files; the SHA-256 of each PDF is stored in the fixture for P08.
- **Done when:** a re-run changes no file, and the box for "4.104,00 €" on the rent adjustment matches its rendered position.

### P04 · Cost Memory — MUST
- **CostItem:** counterparty, `cost_ref` (`RENT`, `UTIL`, `XFER`, or the supplier), expected amount and currency, unit, sources (`STATEMENT`, `CONTRACT`, `PRICE_LIST`, `INVOICE`) with evidence refs.
- Built from Phase 1's recurring detection (B04) on the rows before `as_of`, plus the fixture's documents and price lists. It is **base data** like the imported statement (rebuilt on load and reset); only changeable state is persisted.
- Read API: `GET /api/v1/cost-memory`, `GET /api/v1/documents`, `GET /api/v1/documents/{id}` (metadata, pages, highlight boxes), `GET /api/v1/documents/{id}/file` (the PDF). The mailbox is **not** listed publicly; a document becomes visible when a finding cites it.
- **Done when:** `RENT` expects 380000 (statement + lease), `XFER` expects 416667 CHF (statement + partnership agreement), Fattoria items carry their 2025 list prices.

## Milestone G: check and engine

### P05 · Deterministic engine — MUST
- Formula registry `domain/proof/engine.py`: pure functions with an id, an input schema and an `engine_version`:
  - `variance` (delta and bps), `vpi_threshold_indexation`, `fx_conversion` (reuses `money.convert`), `price_list_check`, `annualize`, `window_impact`.
- `window_impact` runs the Phase 1 forecast engine twice (billed vs. justified amounts) and returns the difference in outflows inside the window and in the lowest balance.
- Percentages round half-up to whole basis points.
- **Done when:** the demo numbers above come out exactly: 306 bps, justified 380000, disputed 30400 / 364800, window 91200, CHF 432500 / 433334.

### P06 · CHECK — MUST
- Input: a document's line items, or the new statement lines of a recurring item. Output: no finding, `verified`, or `unexplained`.
- **Fixed recurring costs:** billed amount vs. the CostItem, ±2 % as in B04 (CHF compares the CHF amount).
- **Supplier invoices:** unit price per line vs. the price list, else the last invoice. A line never seen before is a difference.
- **Known reasons** (→ `verified` with evidence, no agent): the exchange rate written in the statement row, a stored price list. **CHECK never interprets clauses**: choosing and applying a clause is the investigation's job.
- At load and reset, CHECK runs on the preloaded documents and the statement rows before `as_of`.
- **Done when:** rent adjustment → `unexplained` (30400, 800 bps); CHF tranche Apr → `verified` (rate); FLV-94818 → `verified` (price list); AS-11607 → `unexplained` (new line); utilities → no finding.

### P07 · Findings — MUST
- **Finding** as in the blueprint's schema: classification and history, status, counterparty, `cost_ref`, billing period, source document, line item, trace (via `run_id`), financial impact, evidence refs, propagation, recommendation ids.
- **Status:** `INGESTED → EXTRACTED → CHECKED → (INVESTIGATING) → CLASSIFIED → PENDING_APPROVAL → RESOLVED`; every transition is a trace step.
- Persisted behind the repository (in memory and PostgreSQL), `SCHEMA_VERSION` → 5.
- `GET /api/v1/findings?classification=&status=`, `GET /api/v1/findings/{id}`.
- **Done when:** findings round-trip in both repositories, and a reset recreates the preloaded findings identically.

### P08 · Document upload — MUST
- `POST /api/v1/documents` (multipart PDF, size limit, `Idempotency-Key`; `fastapi[standard]` already brings multipart support).
- The file is matched by SHA-256 against the fixture and gets its pre-baked line items (LLM extraction is P20). Then CHECK; an `unexplained` finding starts an investigation run.
- Returns `202 {document_id, finding_ids, run_id | null}`. Unknown file → `DOCUMENT_UNKNOWN` (422); the same document again → `DOCUMENT_ALREADY_PROCESSED` (409).
- **Done when:** uploading `doc_rent_adjustment_2025_06` creates the rent finding and an investigation run; a random PDF returns 422.

## Milestone H: investigation

### P09 · Investigation tools — MUST
- Same registry and route as B22, with tools **scoped by run kind**: an investigation run can't call Cash tools and the reverse (`UNKNOWN_TOOL`). Limit per kind: `INVESTIGATION_MAX_TOOL_CALLS=6`.
- Tools (read-only, deterministic over the fixture):
  - `search_inbox(query, counterparty, date_range)`, `search_documents(query, doc_type)`: keyword search with a fixed ranking, no new dependency;
  - `read_clause(doc_id, section)`: text, page and box;
  - `get_reference_index(name, periods)`: VPI or EUR/CHF, flagged as demo values;
  - `get_price_list(supplier, item, date)`;
  - `compute(formula_id, inputs)`: P05.
- Every result carries the evidence ids it returns; every call is a trace step with a plain label ("Searching the inbox for letters from Main Street Real Estate").
- **Done when:** the hero's 4-step sequence returns the expected results, the hero query ranks the landlord's letter first among the ~20 emails, and a 7th call returns `TOOL_LIMIT_REACHED`.

### P10 · Investigation runs — MUST
- Runs get a `kind` (`CASH`, `INVESTIGATION`) and an optional `finding_id`; new trigger `DOCUMENT_UPLOADED`.
- Start through the AI service (`POST ai/v1/agent/runs` with the kind and the finding's facts; contract to agree with the AI team). One run at a time (`RUN_IN_PROGRESS`), 60 s deadline, the same fallback reasons as Phase 1.
- `/complete` for an investigation: `{classification, formula_id, formula_inputs, evidence_refs[], explanation, letter_draft?, model, prompt_version, tool_calls}`; wrong shape → 422.
- **Done when:** with a fake AI service: the AGENT path, a timeout → FALLBACK, `/fail` → FALLBACK and a late callback → `RUN_CLOSED`.

### P11 · Grounding check and final label — MUST
- A citation that no tool returned in this run is dropped and noted in the trace.
- The backend recomputes the chosen formula from the values the tools returned; numbers in the completion are ignored.
- **Final label:**
  - billed above the justified amount → `conflict`;
  - billed equal to the justified amount → `verified`;
  - no usable evidence left → `unexplained` (escalated), with the list of what was searched.
- **Done when:** a completion citing a document no tool returned loses that citation; a completion claiming `verified` for the rent ends as `conflict` with the engine's numbers.

### P12 · Scripted investigation (FALLBACK) — MUST
- A fixed hypothesis order per cost type (H1 notice, H2 clause, H3 reference or price list, H4 quantity) calling the same tools, with `source: BACKEND` trace steps.
- Used when the agent fails, and at load and reset for preloaded `unexplained` findings (Asimex).
- **Done when:** the FALLBACK hero gives the same label and numbers as the AGENT path; Asimex ends `unexplained` (escalated) with its search list.

## Milestone I: propagate and act

### P13 · Propagation — MUST
- Per finding: the payment decision (keep, or the new amount), the forecast effect (P05 `window_impact`) and the disputed amounts.
- A `verified` change with a new amount proposes an update of the recurring item's future outflows.
- **Done when:** hero → keep `RENT` at 380000, window impact 91200, S0 unchanged; a variant with the VPI above the threshold proposes the indexed amount for `RENT-06…08`.

### P14 · Actions, policy and execution — MUST
- New recommendation kinds `KEEP_PAYMENT`, `UPDATE_RECURRING_AMOUNT`, `SEND_LETTER`, linked to their finding.
- **Policy**, through the same checks and lanes as B16:
  - amount changes on protected categories (`PAYROLL`, `SOCIAL_SECURITY`, `TAX`) → `NOT_ALLOWED`;
  - letters and amount changes → always `NEEDS_APPROVAL` (they contact a third party or move money);
  - pause respected.
- **Executor:** a new `execute_amount_change` in `decisions.py`, next to `execute_move` / `execute_credit` (still the only code that changes the schedule); it bumps `financial_version`. Letters are marked sent with a `SIM-…` confirmation.
- Approve and reject through the existing `/recommendations/{id}/approve` and `/reject`, with receipts. The finding is `RESOLVED` once all its actions are decided.
- **Done when:** approving the hero's actions resolves the finding with receipts and leaves the forecast unchanged; the VPI-above-threshold variant changes `RENT-06…08` and the forecast.

### P15 · Weekly digest and overview — MUST
- `GET /api/v1/digest`: the findings of the period, counts (handled quietly / need the owner), one row each with label, short reason and staged actions.
- `GET /overview` gets a Proof block: open findings by label and the latest investigation run.
- **Done when:** after reset the digest shows CHF and Fattoria `verified` and Asimex escalated; after the hero upload it shows 4 changes, 2 handled quietly, 2 needing the owner.

### P16 · Letter text — SHOULD
- The German clarification letter: the agent's `letter_draft` when the run succeeded, else a backend template citing the clause, the justified amount and a request for the basis of the change. Neutral wording, never a refusal to pay.
- **Done when:** with the AI down, the hero letter comes from the template with `source: TEMPLATE`.

## Milestone J: delivery

### P17 · Contracts and OpenAPI — MUST
- DATA_CONTRACTS: Finding, CostItem, Document (with highlight boxes), Digest, the new recommendation kinds, the investigation start/complete payloads (§3) and the new error codes. ARCHITECTURE: the investigation sequence. `make schema`.
- **Done when:** `test_openapi.py` passes and the AI team has agreed the §3 payloads.

### P18 · Demo reset for both phases — MUST
- Reset rebuilds Cost Memory, deletes uploads, findings and investigation runs, and recreates the preloaded findings (P06, P12). Still under 5 s on PostgreSQL.
- **Done when:** two resets give identical findings and digest, and the Phase 1 S0 numbers are unchanged.

### P19 · Scenario test — MUST (release gate)
- Through the public API: reset → digest (2 quiet, 1 escalated) → upload the rent adjustment → investigation run `SUCCEEDED` → finding `conflict` with the demo numbers → approve → `RESOLVED`, receipts → digest counts → reset. The Phase 1 scenario (B30) still passes after it.
- Must pass in FALLBACK and AGENT (fake AI) modes.

### P20 · LLM extraction of uploads — MIGHT
- An unknown PDF is sent to the AI service for line-item extraction; the backend validates totals and the counterparty before CHECK.

### P21 · Re-check on contract upload — MIGHT
- Uploading a lease or contract updates Cost Memory and re-runs CHECK on the payments it covers.

### P22 · MCP for the investigation tools — MIGHT
- With B26: the investigation tools on `/internal/mcp`.

---

## Summary (Phase 2)

| Milestone | Features | Result |
|---|---|---|
| F · Dataset & Cost Memory | P01–P04 | Design doc, fixture validated against the statement, PDFs with exact highlights, Cost Memory API |
| G · Check & engine | P05–P08 | Formulas, deterministic CHECK, findings, live upload |
| H · Investigation | P09–P12 | Tools, investigation runs, grounding check, fallback |
| I · Propagate & act | P13–P16 | Payment decision, forecast impact, approvals, digest, letter |
| J · Delivery | P17–P22 | Contracts, reset, scenario test, extras |
