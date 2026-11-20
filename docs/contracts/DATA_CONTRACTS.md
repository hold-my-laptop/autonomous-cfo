# Vero: data contracts

The data exchanged between the parts of Vero (Phase 1, Vero Cash, demo data Pizzeria Amalfi):

1. **Frontend ↔ backend** (`apps/web` ↔ `services/api`): the public API under `/api/v1`.
2. **Backend ↔ AI** (`services/api` ↔ `services/ai`): internal traffic in **both directions**.
   - The backend **starts agent runs** and **asks for texts** (backend → AI).
   - The agent **calls tools hosted by the backend** and **reports its proposals** back (AI → backend).

```
apps/web ──/api/v1──▶ services/api ──start run / explain──▶ services/ai ──▶ LLM (vLLM on OpenShift AI)
                       │    ▲                                   │
                       │    └──── tool calls, proposals ────────┘   (/internal/v1, MCP)
                       └──▶ PostgreSQL (only the backend touches the database)
```

**Principle (Option B):** the backend owns **all data and all domain logic**: statement import, revenue model, forecast, planner, credit sizing, policy, execution and receipts. The AI service is a **thin agent runtime**: prompts, the tool-calling loop and the LLM. It reaches data only through the backend's **read-only tools**, and it can never execute anything.

---

## 0. Conventions (apply everywhere)

| Topic | Rule |
|---|---|
| **Money** | A string holding an integer number of **cents**: `"125050"` = 1 250,50 €. Never floats. Negative allowed (`"-2276082"`). At most 15 digits (`^-?[0-9]{1,15}$`), otherwise `VALIDATION_ERROR` |
| **Currency** | Account currency **EUR**. Foreign-currency items (CHF tranches) also carry `original_amount`, `original_currency` and `fx_rate` |
| **Business date** | `"YYYY-MM-DD"`, Europe/Berlin |
| **Timestamp** | UTC RFC 3339, e.g. `"2025-05-05T09:15:00Z"` |
| **IDs** | UUID strings, plus a short human `ref` (`"CHF-06"`, `"PAY-06"`) where useful |
| **Naming** | `snake_case` fields, `UPPER_SNAKE` enum values |
| **"Today"** | `as_of_date` from the data (demo clock: `2025-05-05`), never the system clock |
| **Days** | Calendar days (the bank books debits on weekends too) |
| **Simulation** | Every executed action has `execution: "SIMULATED"` |
| **Errors** | `{ code, title, detail }`, see [Problem](#problem) |
| **Commands** | Every POST that changes state sends an `Idempotency-Key` header (a UUID per user action) |

Types used below: `Money`, `Date`, `Timestamp`, `UUID`, `int`, `bool`, `string`, `T[]` (list), `T?` (may be null).

---

## 1. Shared data types

### Transaction
One imported bank statement line.

| Field | Type | Description |
|---|---|---|
| `id` | UUID | |
| `booking_date` | Date | |
| `type` | string | Bank's transaction type, e.g. `CREDIT CARD PAYOUT` |
| `counterparty` | string | Beneficiary/payer |
| `reference` | string | Payment reference |
| `amount` | Money | In EUR (converted for CHF rows) |
| `original_amount` | Money? | CHF amount for CHF rows |
| `original_currency` | string? | `CHF` |
| `fx_rate` | string? | e.g. `"1.040"` (EUR per CHF), parsed from the reference |
| `category` | enum | `CARD_REVENUE`, `VARIABLE_COST`, `RENT`, `PAYROLL`, `SOCIAL_SECURITY`, `TAX`, `UTILITIES`, `PARTNER_TRANSFER`, `OTHER` |
| `recurring_id` | UUID? | Link to the detected recurring payment, if any |

### Outflow
A known future payment in the forecast window (from a detected recurring payment or entered by the owner).

| Field | Type | Description |
|---|---|---|
| `id` | UUID | |
| `ref` | string | e.g. `CHF-06` |
| `counterparty` | string | |
| `category` | enum | `RENT`, `PAYROLL`, `SOCIAL_SECURITY`, `TAX`, `UTILITIES`, `PARTNER_TRANSFER`, `SUPPLIER`, `OTHER` |
| `amount` | Money | EUR (estimated at the last rate for CHF) |
| `original_amount` / `original_currency` / `fx_rate` | Money? / string? / string? | For CHF tranches: `"416667"`, `CHF`, `"1.040"` |
| `due_date` | Date | Original date |
| `scheduled_date` | Date | When it will be paid. Equals `due_date` unless moved (simulated) |
| `latest_allowed_date` | Date? | Latest date it may move to. `null` = not movable |
| `protected` | bool | True if the mandate protects its category |
| `description` | string? | What the payment is when the counterparty alone doesn't say, e.g. "Income tax prepayment" |
| `source` | enum | `DETECTED` (from statement history) or `OWNER` (entered, e.g. tax schedule) |
| `version` | int | Increments on every change |

### Inflow
A known expected incoming payment (other than card revenue, which comes from the revenue model). Empty in the demo, but kept for owner input.

| Field | Type | Description |
|---|---|---|
| `id`, `ref`, `counterparty` | UUID, string, string | |
| `amount` | Money | |
| `expected_date` | Date | |
| `original_expected_date` | Date | |

### RevenueModel
How Vero estimates daily card revenue and variable costs.

| Field | Type | Description |
|---|---|---|
| `method` | string | `WEEKDAY_AVERAGE` |
| `window_from`, `window_to` | Date | e.g. `2025-03-10` – `2025-05-04` (8 weeks) |
| `weeks` | int | Number of weeks averaged, e.g. `8` |
| `by_weekday` | `{ weekday: "MON".."SUN", revenue: Money, variable_costs: Money }[]` | Averages per weekday (costs negative) |
| `model_version` | string | e.g. `revenue-1.0.0` |

### PlannedEvent
Something the owner tells Vero about the future.

| Field | Type | Description |
|---|---|---|
| `id` | UUID | |
| `kind` | enum | `CLOSURE` |
| `date_from`, `date_to` | Date | e.g. `2025-06-01` – `2025-06-30` |
| `revenue_factor` | string | `"0"` = no revenue (Phase 1 supports only 0) |
| `variable_costs_stop` | bool | True: supplier and market purchases stop too |
| `note` | string? | |

### Mandate
The owner's rules for what Vero may do alone (one version).

| Field | Type | Description |
|---|---|---|
| `version` | int | 1, 2, … |
| `status` | enum | `DRAFT`, `ACTIVE`, `SUPERSEDED` |
| `cash_floor` | Money | **R1.** Lowest balance allowed (demo: `"-500000"`, the existing overdraft) |
| `act_alone_enabled` | bool | **R2.** If false, every move needs approval |
| `max_delay_days` | int | **R2.** Max days a payment may move (0–90) |
| `max_amount_per_action` | Money | **R3.** Above this, a move needs approval |
| `max_amount_per_day` | Money | **R3.** Total Vero may move alone per decision day |
| `protected_categories` | enum[] | **R4.** Never movable. Always includes `PAYROLL`, `SOCIAL_SECURITY`, `TAX` |
| `credit_enabled` | bool | **R5.** May Vero propose a credit line (always needs approval) |
| `max_credit_amount` | Money | **R5.** Upper limit for a proposed line |
| `paused` | bool | When true, everything needs approval |
| `note` | string? | |
| `created_at` / `activated_at` | Timestamp / Timestamp? | |

### Forecast

| Field | Type | Description |
|---|---|---|
| `as_of_date` | Date | Day 0 |
| `horizon_days` | int | 91 |
| `start_balance` | Money | Balance at the start of `as_of_date` |
| `cash_floor` | Money | Floor used |
| `summary` | ForecastSummary | Headline numbers |
| `days` | ForecastDay[] | One row per day (91) |
| `drivers` | Driver[] | Why it goes below the floor |
| `assumptions` | Assumption[] | What the numbers assume |
| `planned_events` | PlannedEvent[] | Events applied |
| `credit_lines` | CreditLine[] | Approved lines included as headroom |
| `engine_version` | string | e.g. `forecast-1.0.0` |

**ForecastDay**

| Field | Type | Description |
|---|---|---|
| `date` | Date | |
| `revenue` | Money | Estimated card revenue (0 on closure days) |
| `variable_costs` | Money | Estimated variable costs (0 on closure days) |
| `fixed_outflows` | Money | Known outflows that day |
| `other_inflows` | Money | Known inflows that day |
| `closing` | Money | End-of-day balance (may be negative, never clamped) |
| `credit_available` | Money | Approved credit-line headroom available that day (0 if none) |
| `below_floor` | bool | `closing + credit_available < cash_floor` (computed by the backend, not the UI) |
| `closed` | bool | Inside a planned closure |

**ForecastSummary**

| Field | Type | Description |
|---|---|---|
| `first_breach` | Breach? | First day below the floor, or `null` |
| `lowest` | `{ date: Date, closing: Money }` | Lowest balance |
| `days_below_floor` | int | |
| `end_balance` | Money | Balance on the last day |
| `headroom_at_lowest` | Money | `lowest.closing + credit_available − cash_floor` |

**Breach**: `{ date: Date, closing: Money, shortfall: Money }`, where `shortfall = cash_floor − (closing + credit_available)`.

**Driver**

| Field | Type | Description |
|---|---|---|
| `kind` | enum | `NO_REVENUE` (closure), `OUTFLOW` (big payment before the breach), `MISSING_INFLOW` |
| `ref` | string? | e.g. `PAY-06` |
| `label` | string | e.g. "Closed 1–30 Jun: no card revenue" |
| `amount` | Money | e.g. `"4590578"` lost revenue |
| `date_from`, `date_to` | Date | |

**Assumption**: `{ code: string, text: string }`. Codes are generated from the inputs: `REVENUE_WEEKDAY_AVERAGE_<N>W` and `CALENDAR_DAYS` and `CLOSING_DAY_CONVENTION` (always), `<CURRENCY>_AT_LAST_RATE` (foreign-currency outflows, e.g. `CHF_AT_LAST_RATE`), `OWNER_SCHEDULE` (owner-entered outflows), `CLOSURE_STOPS_VARIABLE_COSTS` (a closure that also stops supplier spending).

### Backtest *(demo)*
Forecast vs what really happened (possible because the sample covers all of 2025). Actual balances use the same opening balance and floor, without credit lines. The forecast side leaves out Vero's simulated actions (every payment on its due date, no credit lines), because they never happened in reality: the numbers stay those of the plan as the owner made it (e.g. S1) after moves or approvals.

| Field | Type | Description |
|---|---|---|
| `forecast_id` | UUID | The current forecast (same as `Forecast.id`) this backtest belongs to |
| `complete` | bool | False if the statement ends before the forecast window does |
| `days` | `{ date: Date, forecast: Money, actual: Money }[]` | |
| `forecast`, `actual` | `{ first_breach: Breach?, lowest: { date, closing }, days_below_floor: int, end_balance: Money }` | e.g. lowest 3 Jul −22 760,82 € vs 3 Jul −22 097,60 € |
| `mean_abs_error` | Money | Average daily difference |
| `lowest_error` | Money | Forecast lowest − actual lowest (e.g. `"-66322"`) |
| `lowest_error_bp` | int | Error at the lowest point in basis points (300 = 3,00 %) |
| `note` | string | "Backtest on the sample year: …" |

### TraceStep
One line of the agent's visible trace.

| Field | Type | Description |
|---|---|---|
| `seq` | int | 1, 2, … |
| `kind` | enum | `THINK`, `TOOL_CALL`, `TOOL_RESULT`, `POLICY`, `EXECUTE`, `SUMMARY`, `FALLBACK` |
| `source` | enum | `AGENT` (the AI's own note) or `BACKEND` (tool calls, policy, execution) |
| `label` | string | Plain-language line, e.g. "Recomputing your cash for the next 91 days" |
| `tool` | string? | Tool name if it's a tool call |
| `data` | object? | Structured input/output for a "details" view |
| `at` | Timestamp | |

### MoveCandidate
A payment the planner suggests moving. A suggestion only, not authorized.

| Field | Type | Description |
|---|---|---|
| `outflow_id` | UUID | |
| `ref` | string | e.g. `CHF-06` |
| `counterparty` | string | |
| `amount` | Money | |
| `from_date` | Date | Current scheduled date |
| `to_date` | Date | Proposed date (its `latest_allowed_date`) |
| `benefit` | `{ first_breach_delay_days: int, days_below_floor_saved: int }` | Why it helps |

### CreditLine
A short-term credit line sized to cover the gap that remains. The terms are illustrative.

| Field | Type | Description |
|---|---|---|
| `amount` | Money | Shortfall rounded **up to 5 000 €**, e.g. `"2000000"` |
| `available_from` | Date | First remaining breach day |
| `available_until` | Date | Illustrative term, e.g. `2025-09-30` |
| `shortfall` | Money | Exact gap at the lowest point |
| `rounded_to` | Money | `"500000"` |
| `headroom_at_lowest` | Money | Unused headroom at the lowest point if approved |

### Problem
The error body for every 4xx/5xx response, sent as `application/problem+json` (RFC 9457).

| Field | Type | Description |
|---|---|---|
| `type` | string | `urn:vero:problem:<code in lower case>`, e.g. `urn:vero:problem:version_conflict` |
| `title` | string | Short human title |
| `status` | int | HTTP status |
| `code` | string | See the table below |
| `detail` | string? | Explanation |
| `current` | object? | Current state on a conflict, e.g. `{ "financial_version": 5 }` |
| `errors` | `{ field, message }[]`? | For `VALIDATION_ERROR`: each invalid field, e.g. `body.note`, `query.category` |

| `code` | HTTP | When |
|---|---|---|
| `VALIDATION_ERROR` | 422 | Invalid body, query or header (incl. a non-UUID `Idempotency-Key`) |
| `IDEMPOTENCY_KEY_MISSING` | 400 | State-changing POST without `Idempotency-Key` |
| `IDEMPOTENCY_KEY_REUSED` | 409 | Same key, different request |
| `VERSION_CONFLICT` | 409 | `expected_financial_version` / `expected_active_version` is stale; `current` has the latest |
| `STALE_RECOMMENDATION` | 409 | Data changed since the recommendation was made |
| `NOT_PENDING` | 409 | Recommendation already decided |
| `RUN_IN_PROGRESS` | 409 | An agent run is still active (only one at a time) |
| `MANDATE_INVALID` | 422 | e.g. removing a protected category |
| `IMPORT_INVALID` | 422 | Unreadable statement file |
| `NOT_FOUND` / `METHOD_NOT_ALLOWED` | 404 / 405 | Unknown path or method |
| `DEMO_DISABLED` | 403 | `/demo/*` while `DEMO_MODE` is off |
| `STATE_NOT_LOADED` | 503 | Data not loaded (e.g. statement missing) |
| `UNAUTHORIZED` | 401 | `/internal/v1` without a valid `Bearer INTERNAL_TOKEN` |
| `RUN_CLOSED` | 409 | Agent callback for a run that is no longer running |
| `HTTP_ERROR` | other | Any other HTTP error |
| `INTERNAL_ERROR` | 500 | Unexpected server failure (logged; no internal details in the body) |

**Idempotency:** a repeated request with the same key and body returns the original response without repeating the change. Only successful responses are stored, so a request rejected with `VERSION_CONFLICT` can be retried with the same key after refreshing. A demo reset forgets all keys.

---

## 2. Frontend ↔ backend

### 2.1 What is exchanged

| # | When | Frontend sends | Backend returns |
|---|---|---|---|
| 1 | Dashboard opens or refreshes | `GET /overview` | **Overview** |
| 2 | Chart / details | `GET /forecast` (`?weekly=true` optional) | **Forecast** (+ `id`, `label: "CURRENT"`, `financial_version`, `mandate_version`, optional 13 `weeks`: `{ week, start_date, end_date, inflow, outflow, closing, lowest, any_below_floor }`) |
| 3 | *(demo)* Backtest overlay | `GET /forecast/backtest` | **Backtest** |
| 4 | Money in and out | `GET /outflows`, `GET /inflows`, `GET /revenue-model` | **Outflow[]**, **Inflow[]**, **RevenueModel** |
| 5 | Statement view | `GET /transactions?from=&to=&category=` | **Transaction[]** |
| 6 | Import a statement | `POST /imports/bank-statement` (multipart `file`, CSV) | **202** `{ import_id, run_id, transactions_imported }` |
| 7 | Planned events | `GET /planned-events` | **PlannedEvent[]** |
| 8 | Rules screen | `GET /mandate`, `GET /mandate/versions` | **Mandate**, **Mandate[]** |
| 9 | Owner edits / activates / pauses rules | `POST /mandate` (**MandateIn**), `POST /mandate/{version}/activate` (`{ expected_active_version }`), `POST /mandate/pause` / `resume` | **Mandate** |
| 10 | Something changes (e.g. "we close in June") | `POST /events` with **EventIn** | **202** `{ run_id, status }`: the run's current status (`QUEUED`/`RUNNING`, or `SUCCEEDED` if it already finished) |
| 11 | Watching the agent work | `GET /runs/{run_id}` (poll ~500 ms) | **Run** |
| 12 | Recommendations | `GET /recommendations?lane=&status=` (newest first), `GET /recommendations/{id}` | **Recommendation[]** / **Recommendation** |
| 13 | Owner approves / rejects | `POST /recommendations/{id}/approve` or `/reject` with `{ note? }` | **DecisionResult** |
| 14 | Owner asks to move a payment | `POST /outflows/{id}/reschedule-requests` with `{ to_date, note? }` | **202** `{ run_id, status }`. The same rules apply with the owner's approval implied: payroll → `BLOCKED` (NOT_ALLOWED), a movable payment is moved (`APPROVED_EXECUTED`) |
| 15 | Evidence | `GET /receipts/{id}` | **Receipt** |
| 16 | Health / model check | `GET /health/live`, `/health/ready`, `/health/model` | live: `{ status: "ok" }` · ready: `{ status: "ok" \| "unavailable", state_loaded: bool }` (503 when not loaded) · model: **ModelHealth** |
| 17 | Demo reset | `POST /demo/reset` | `{ reset: true, as_of_date, financial_version }` |
| 18 | *(SHOULD)* Rules from a sentence | `POST /mandate/draft-from-text` with `{ text }` | **MandateDraft** |

### 2.2 Data types

#### Company

| Field | Type | Description |
|---|---|---|
| `name` | string | "Pizzeria Amalfi" |
| `owner` | string | "Vincenzo Amalfi" |
| `currency` | string | `EUR` |
| `as_of_date` | Date | The demo's "today" (`2025-05-05`) |
| `balance` | Money | Balance at the start of `as_of_date` |
| `statement_until` | Date | Last imported booking date |
| `financial_version` | int | Send back as `expected_financial_version` on `POST /events` |
| `mandate_version` | int | |
| `paused` | bool | |

#### Overview

| Field | Type | Description |
|---|---|---|
| `company` | Company | |
| `cash_floor` | Money | |
| `forecast_summary` | ForecastSummary | |
| `headline` | Explanation | One-sentence summary |
| `lanes.ANALYSED` | Finding[] | Facts Vero found alone |
| `lanes.DONE_WITHIN_MANDATE` | Recommendation[] | Done alone (simulated) |
| `lanes.NEEDS_APPROVAL` | Recommendation[] | Waiting for the owner |
| `lanes.NOT_ALLOWED` | Recommendation[] | Refused by the rules |
| `pending_count` | int | |
| `latest_run` | `{ run_id, status }?` | |

**Finding**: `{ kind: "DRIVER" | "RECURRING" | "FX_EXPOSURE" | "BACKTEST", text: string }`, e.g. "4 CHF tranches so far cost 4 325,00–4 354,17 € (rate 1,038–1,045)".

**Explanation**: `{ text: string, source: "MODEL" | "REPLAY" | "TEMPLATE" }`.

#### MandateIn
The same fields as **Mandate** minus `version`, `status`, `created_at` and `activated_at`. Unknown fields are rejected. Removing `PAYROLL`, `SOCIAL_SECURITY` or `TAX` from `protected_categories` returns `MANDATE_INVALID`.

#### MandateDraft *(SHOULD)*
`{ draft: MandateIn, diff: { field, old, new }[], uncertain: string[], unsupported: string[], source: "MODEL" | "REPLAY" | "STUB" }`

#### EventIn

| Field | Type | Description |
|---|---|---|
| `type` | enum | `CLOSURE_PLANNED`, `OUTFLOW_ADDED`, `INFLOW_ADDED`, `INFLOW_DELAYED` |
| `expected_financial_version` | int | From `Company.financial_version`. A mismatch returns `VERSION_CONFLICT` |
| `payload` | object | Depends on `type` (below) |

| `type` | `payload` fields |
|---|---|
| `CLOSURE_PLANNED` | `date_from: Date`, `date_to: Date`, `variable_costs_stop: bool` (default true), `note: string?` |
| `OUTFLOW_ADDED` | `counterparty`, `category`, `amount: Money`, `due_date: Date`, `latest_allowed_date: Date?` |
| `INFLOW_ADDED` | `counterparty`, `amount: Money`, `expected_date: Date` |
| `INFLOW_DELAYED` | `inflow_id: UUID`, `new_expected_date: Date` |

Statement imports (`POST /imports/bank-statement`) create a `STATEMENT_IMPORTED` event themselves.

#### Run

| Field | Type | Description |
|---|---|---|
| `run_id` | UUID | |
| `status` | enum | `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED` |
| `mode` | enum | `AGENT` or `FALLBACK`. Show it |
| `trigger` | `{ type, label }` | e.g. "You plan to close 1–30 June" |
| `trace` | TraceStep[] | Grows while running |
| `result` | RunResult? | When `SUCCEEDED` |
| `error` | Problem? | When `FAILED` |
| `created_at`, `finished_at` | Timestamp, Timestamp? | |

**RunResult**: `forecast_id: UUID`, `recommendation_ids: UUID[]`, `headline: Explanation?`, `counts: { done_within_mandate, needs_approval, not_allowed }` (by lane), `gap_closed: bool?` (no day below the floor after the run). `GET /runs?limit=` lists runs newest first.

#### Recommendation

| Field | Type | Description |
|---|---|---|
| `id`, `run_id` | UUID | |
| `kind` | enum | `RESCHEDULE_PAYMENT`, `CREDIT_LINE` |
| `lane` | enum | `DONE_WITHIN_MANDATE`, `NEEDS_APPROVAL`, `NOT_ALLOWED` (fixed when proposed) |
| `status` | enum | `EXECUTED` (done alone), `PENDING`, `APPROVED_EXECUTED`, `REJECTED`, `BLOCKED` (refused by the rules), `STALE` (data changed before the decision), `SUPERSEDED` (a newer run replaced it) |
| `execution` | `"SIMULATED"` | Always. Show a "Simulated" badge |
| `subject` | RescheduleSubject \| CreditLineSubject | By `kind` (discriminator `subject.kind`) |
| `reason` | `{ first_breach: Date?, shortfall: Money, requested_by?: "OWNER" }` | The problem it addresses. `requested_by` is set for owner reschedule requests |
| `checks` | Check[] | Every rule checked, including passes |
| `impact` | `{ before: ImpactState, after_if_executed: ImpactState }` | |
| `explanation` | Explanation | |
| `receipt_ids` | UUID[] | Oldest first: the PROPOSAL receipt, then the DECISION receipt if any |
| `executed` | `{ execution: "SIMULATED", confirmation_ref: "SIM-…", executed_by: "VERO" \| "OWNER", executed_at: Timestamp }?` | When it was executed |
| `note` | string? | Owner's note on the decision |
| `created_at`, `decided_at` | Timestamp, Timestamp? | |

**RescheduleSubject**: `kind: "RESCHEDULE_PAYMENT"`, `outflow_id`, `ref`, `counterparty`, `category`, `amount`, `original_amount?`, `original_currency?`, `from_date`, `to_date`.

**CreditLineSubject**: `kind: "CREDIT_LINE"`, `product: "SHORT_TERM_CREDIT_LINE"`, `amount`, `available_from`, `available_until`, `shortfall`, `rounded_to`, `headroom_at_lowest`, `illustrative: true` (show "illustrative, not a bank offer").

**Check**: `{ rule: "R1".."R5" | "VALIDITY" | "PAUSE", result: "PASS" | "FAIL" | "INFO", effect: "NEEDS_APPROVAL" | "NOT_ALLOWED" | null, text: string }` (`effect` only on FAIL), e.g. "4 333,34 € is within your 5 000 € limit for acting alone".

**ImpactState**: `{ first_breach: Date?, lowest: Money, lowest_date: Date, days_below_floor: int }`.

#### DecisionResult
Response to `POST /recommendations/{id}/approve` and `/reject` (body `{ note? }`, `Idempotency-Key` required).

| Field | Type | Description |
|---|---|---|
| `recommendation` | Recommendation | Updated status; `executed` set when approved |
| `forecast_summary` | ForecastSummary | Cash after the decision |
| `receipt_id` | UUID | The DECISION receipt |
| `follow_up_recommendation_ids` | UUID[] | New recommendations made because this approval changed the data, e.g. the pending credit line resized after a move was approved (the old one becomes `SUPERSEDED`) |

Errors: `NOT_FOUND` (404), `NOT_PENDING` (409, already decided), `STALE_RECOMMENDATION` (409; the recommendation is also marked `STALE`, plan again). An approval re-checks the other pending items of the same run instead of making them stale: a pending move keeps its lane with updated impact; a pending credit line is kept if it still fits, otherwise replaced by a follow-up or dropped when no gap is left. Approving re-checks the rules with the owner's approval: protected payments and invalid dates are still refused, and the recommendation becomes `BLOCKED`.

#### Receipt
Immutable evidence, from `GET /receipts/{id}`.

| Field | Type | Description |
|---|---|---|
| `id`, `recommendation_id` | UUID | |
| `kind` | enum | `PROPOSAL` (Vero proposed or acted) or `DECISION` (owner decided or requested) |
| `created_at` | Timestamp | |
| `trigger` | string | What started it, e.g. "You plan to close 1 Jun – 30 Jun" or "Your decision" |
| `subject` | RescheduleSubject \| CreditLineSubject | |
| `lane` | enum | |
| `decision` | enum? | `APPROVED`, `REJECTED`, `REQUESTED` (owner reschedule request) |
| `checks` | Check[] | |
| `before`, `after` | ImpactState | |
| `assumptions` | string[] | Assumption codes of the forecast used |
| `mandate_version`, `financial_version` | int | |
| `versions` | map string→string | `engine`, `planner`, `policy`, `revenue_model` |
| `execution` | `"SIMULATED"` | |
| `explanation` | Explanation | |

#### ModelHealth
`{ ok: bool, model: string, mode: "live" | "replay" | "stub"?, latency_ms: int, error: string? }`, passed through from the AI service. `200` when `ok`, otherwise `503`. When the AI service itself cannot be reached, `model` is `"unknown"`, `mode` is `null` and `error` says so. Without `INTERNAL_TOKEN` (agent mode off) it is always `503`. Show `mode`: only `live` means the model on OpenShift AI answered.

---

## 3. Backend ↔ AI

The backend owns data and logic. The AI service runs the agent: it **reads through tools** and **proposes**. The backend **checks, decides lanes, executes (simulated) and stores**. If the AI service fails, the backend completes the run on its own (**FALLBACK**).

**Internal security:** all calls in both directions use the cluster-internal network and `Authorization: Bearer <INTERNAL_TOKEN>`. `/internal/v1` is **not** routed to the browser.

### 3.1 What is exchanged

**Backend → AI service** (`AI_URL`, in the cluster `http://ai:8080`)

| # | When | Backend sends | AI returns |
|---|---|---|---|
| 1 | An event arrives | `POST /v1/agent/runs` with **AgentRunStart** | **202** `{ accepted: true }`. The agent then works asynchronously |
| 2 | After policy + execution | `POST /v1/explain` with **ExplainRequest** | **ExplainResult** (sync) |
| 3 | Health | `GET /health/live`, `GET /health/model` | `{ status }` / **ModelHealth** (+ `mode`) |
| 4 | *(SHOULD)* Rules from a sentence | `POST /v1/mandate/draft` with `{ text, current: Mandate }` | **MandateDraftResult** |

**AI service → backend** (`http://api:8080/internal/v1` in the cluster)

| # | When | AI sends | Backend returns |
|---|---|---|---|
| 5 | Agent starts | `GET /tools` | **ToolSpec[]** |
| 6 | Agent calls a tool | `POST /tools/{name}` with **ToolCall** | **ToolResult** (the backend also writes the trace step) |
| 7 | *(optional)* Agent note | `POST /runs/{run_id}/notes` with `{ text }` | `204` |
| 8 | Agent is done | `POST /runs/{run_id}/complete` with **AgentCompletion** | `204` |
| 9 | Agent gave up | `POST /runs/{run_id}/fail` with `{ reason, detail? }` | `204` (backend runs FALLBACK) |

Callbacks for a run that is no longer running (finished, fell back, or past the deadline) return `409 RUN_CLOSED`. A missing or wrong token returns `401 UNAUTHORIZED`. Only one agent run is active at a time: a new event meanwhile returns `409 RUN_IN_PROGRESS`.

**Backend settings:** `AI_URL`, `INTERNAL_TOKEN` (agent mode is on only when it is set; otherwise runs are FALLBACK and synchronous), `AGENT_MAX_TOOL_CALLS` (8), `AGENT_DEADLINE_S` (60), `AGENT_START_TIMEOUT_S` (5), `EXPLAIN_TIMEOUT_S` (10), `MODEL_HEALTH_TIMEOUT_S` (20).

**MCP (bonus criterion, SHOULD):** the same tools are exposed as an **MCP server** at `http://api:8080/internal/mcp`, with identical names, schemas and results.

### 3.2 Tools (hosted by the backend, read-only)

Every call is **bound to a run** and answered from the state at the run's `financial_version`. If the state changed since the run started, the tool returns `STALE_RUN` and the agent must stop.

| Tool | `arguments` | `result` | Trace label (backend) |
|---|---|---|---|
| `get_mandate` | `{}` | **Mandate** | "Reading your rules (v2)" |
| `get_forecast` | `{}` | `{ cash_floor: Money, summary: ForecastSummary, drivers: Driver[], planned_events: PlannedEvent[] }` (no daily rows) | "Recomputing your cash for the next 91 days" |
| `explain_drivers` | `{ date: Date }` | `{ date, closed: bool, revenue_estimate: Money, outflows: Outflow[], days: { date, closing: Money }[] }` within ±7 days | "Looking at what happens around 3 Jul" |
| `get_revenue_model` | `{}` | **RevenueModel** | "Checking your usual weekly takings" |
| `list_outflows` | `{ movable_only?: bool }` | **Outflow[]** | "Listing payments that could wait" |
| `find_reschedule_options` | `{}` | **PlanResult** | "Looking for payments that can wait" |
| `simulate_moves` | `{ moves: { outflow_id, to_date }[] }` | `{ summary: ForecastSummary }`. Moves must be candidates from this run | "Testing 1 move" |
| `size_credit_line` | `{ after_moves?: { outflow_id, to_date }[] }` | `{ needed: bool, credit_line: CreditLine? }` | "Sizing a credit line for what's left" |
| `get_fx_exposure` | `{}` | `{ currency: "CHF", tranches_paid: int, eur_min: Money, eur_max: Money, last_rate: string, upcoming: Outflow[] }` | "Checking your CHF transfers" |
| `check_policy` | `{ proposal: Proposal }` | `{ lane_preview, checks: Check[] }`. Preview only, not binding | "Checking this against your rules" |

*Phase 2 (Vero Proof) will add document tools here.*

### 3.3 Data types

#### AgentRunStart

| Field | Type | Description |
|---|---|---|
| `run_id` | UUID | Use it in every tool call and callback |
| `event` | `{ type, label, payload }` | e.g. `CLOSURE_PLANNED`, "You plan to close 1–30 June" |
| `company` | `{ name, as_of_date, currency }` | Prompt context |
| `limits` | `{ max_tool_calls: int, deadline_s: int }` | Default 8 calls, 60 s |

#### ToolSpec
`{ name: string, description: string (written for the LLM), input_schema: JSON Schema }`

#### ToolCall
`{ run_id: UUID, arguments: object, call_id: string }`. A repeated `call_id` returns the same result.

#### ToolResult
`{ ok: bool, result: object?, error: { code, message }?, financial_version: int, tool_calls_used: int }`
Error codes: `INVALID_ARGUMENT`, `UNKNOWN_TOOL`, `STALE_RUN`, `TOOL_LIMIT_REACHED`, `RUN_CLOSED`. Tool errors come back as `200` with `ok: false`, not as HTTP errors.

#### PlanResult (result of `find_reschedule_options`)

| Field | Type | Description |
|---|---|---|
| `candidates` | MoveCandidate[] | In planner order |
| `resolved` | bool | True if all candidates together keep cash above the floor |
| `residual_breach` | Breach? | Gap left after all candidates |
| `forecast_if_all_applied` | ForecastSummary | |
| `planner_version` | string | e.g. `greedy-1.1.0` |

#### Proposal

| Field | Type | Description |
|---|---|---|
| `kind` | enum | `RESCHEDULE_PAYMENT`, `CREDIT_LINE` |
| `move` | `{ outflow_id: UUID, to_date: Date }`? | Must be one of this run's planner candidates. A repeated payment is discarded |
| `credit_line` | `{ amount: Money, available_from: Date }`? | Must equal this run's `size_credit_line` result. Only one per completion. The backend sizes it again after the moves are applied, since moves waiting for approval have not happened, and notes a resize in the trace |
| `rationale` | string? | Shown as "Vero's note", never as a fact |

#### AgentCompletion
`{ proposals: Proposal[] (ordered), summary_note: string?, model: string, prompt_version: string, tool_calls: int }`

#### Agent failure
`{ reason: "MODEL_TIMEOUT" | "MODEL_ERROR" | "INVALID_TOOL_CALLS" | "NO_FINAL_ANSWER" | "TOOL_LIMIT_REACHED", detail?: string }`

#### ExplainRequest

| Field | Type | Description |
|---|---|---|
| `run_id` | UUID | |
| `trigger_label` | string | |
| `mandate` | Mandate | |
| `forecast_before`, `forecast_after` | ForecastSummary | |
| `items` | ExplainItem[] | One per recommendation |
| `findings` | Finding[] | ANALYSED facts that may be mentioned |
| `language` | enum | `en` (default), `de` |

**ExplainItem**: `recommendation_id`, `kind`, `lane`, `subject`, `failed_checks: { rule, text }[]`, `before: ImpactState`, `after: ImpactState`.

#### ExplainResult
`{ headline: string (≤ 200), items: { recommendation_id, text (≤ 300) }[], source: "MODEL" | "REPLAY" | "TEMPLATE", fallback_reason: string? }`

The backend stores the headline and item texts with `source` (`Run.result.headline`, `Overview.headline`, `Recommendation.explanation`). Items missing from the result keep the backend template. Receipts keep the text from proposal time.

**Rule:** every number and date in the texts must appear in the facts sent, and phrases like "transferred", "sent", "guaranteed" or "approved by the bank" are rejected. Otherwise the AI service returns template texts. If `/v1/explain` fails or times out (10 s), the backend uses its own templates.

#### MandateDraftResult *(SHOULD)*
`{ changes: { only changed Mandate fields }, uncertain: string[], unsupported: string[], source: "MODEL" | "REPLAY" | "STUB" }`

#### ModelHealth (AI)
`{ ok: bool, model: string, mode: "live" | "replay" | "stub", latency_ms: int, error: string? }`

### 3.4 Run timeline

1. **Backend:** stores the event (e.g. `CLOSURE_PLANNED`), creates the run (`QUEUED`), returns `202`, calls `POST ai/v1/agent/runs` (run → `RUNNING`).
2. **AI:** `GET /internal/v1/tools`, then loops: LLM → `POST /internal/v1/tools/{name}`. The backend writes each call to the trace.
3. **AI:** `POST /internal/v1/runs/{id}/complete` (or `/fail`).
4. **Backend:** validates proposals → policy → lanes → executes `DONE_WITHIN_MANDATE` (simulated) → re-forecasts → stores (run still shows `RUNNING`) → `POST ai/v1/explain` outside the transaction → stores the texts → run `SUCCEEDED` (`mode: AGENT`).
5. **FALLBACK** (on `/fail`, the deadline passing, or the AI service being unreachable): the backend runs forecast → planner → credit sizing itself, then step 4 (`mode: FALLBACK`).

---

## 4. Who owns what

| Data | Created / owned by | Read by |
|---|---|---|
| Transaction, RevenueModel, Outflow, Inflow, PlannedEvent, Mandate, Company | Backend (database) | Frontend; AI via tools |
| Forecast, Backtest, PlanResult, CreditLine | Backend (engine in `services/api`) | Frontend; AI via tools |
| TraceStep | Backend (tools, policy, execution) + AI (notes) | Frontend |
| Proposal | AI (agent) | Backend only (validated → Recommendations) |
| Recommendation, Receipt, DecisionResult | Backend | Frontend |
| Explanation texts | AI | Backend (stores), Frontend |

Phase 2 (Vero Proof) will add its types (documents, findings, evidence references) here.
