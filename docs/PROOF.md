# Vero Proof: Phase 2 design

> **Status:** Phase 2 design and acceptance scenarios. The [project README](../README.md) describes the implemented demo and its current limits.
> **Scope:** Phase 2 = **Vero Proof**. When a recurring cost changes, Vero finds out why, proves whether the change is justified, and prepares everything downstream (payment, cash forecast, letter to the supplier) for the owner's approval. Execution is simulated.
> **Demo data:** the same **Pizzeria Amalfi** 2025 bank statement and the same `as_of_date` (5 May 2025) as Phase 1, plus synthetic documents that agree with it (§4).
>
> **Related documents:**
> - [`BACKEND.md`](BACKEND.md#phase-2-vero-proof): features P01–P23, each with its "done when"
> - [`DESIGN.md`](DESIGN.md): Phase 1 (Vero Cash), whose forecast, mandate and policy Vero Proof reuses
> - [`contracts/DATA_CONTRACTS.md`](contracts/DATA_CONTRACTS.md): endpoints and payloads (Phase 2 shapes added with P17)
> - [`ARCHITECTURE.md`](ARCHITECTURE.md): architecture and sequence diagrams (the Proof run sequence added with P17)

---

## 1. What Vero Proof does

> **Accounting software tells you what you paid. Vero proves what you *should* pay, and fixes everything that depends on it.**

SME owners rarely lose money to fraud; they lose it to lag. Recurring costs change for legitimate reasons (an index clause, a new price list, an exchange rate), and each change means work: notice it, check it against the contract, update the payment and the cash plan, and reply to the supplier when it isn't justified. Existing tools report these amounts. Nobody checks them.

**One product, two agents.** Vero Cash (Phase 1) plans the pizzeria's cash with the recurring amounts it detected in the statement. Vero Proof makes sure those amounts are the ones the pizzeria should pay. They share the statement, the mandate, the policy lanes, the runs and trace, the recommendations and receipts, and the model on OpenShift AI.

**The challenge card's distinctions, for Phase 2:**

| The card asks to show | In Vero Proof |
|---|---|
| What agents analyse independently | Reading uploaded documents, comparing them with Cost Memory, investigating unexplained changes, classifying them |
| What they recommend | Keep or change a payment, send a clarification letter |
| What requires user approval | Every payment change and every letter (always `NEEDS_APPROVAL`) |
| Whether execution is simulated | Always simulated (`execution: "SIMULATED"`) |

---

## 2. Roles: who does what

| Who | Does |
|---|---|
| **Owner** | Uploads a document, reads the digest, approves or rejects prepared actions |
| **Backend** | Cost Memory, CHECK (detection), the formula engine, the tools, validation of everything the model returns, the final label, propagation, policy, simulated execution |
| **AI service** | Reads uploaded PDFs with the model (extraction), runs the investigation agent, drafts the letter |
| **Model** (`vero-llm`, Qwen3-8B on OpenShift AI) | Points at printed text when extracting; chooses hypotheses, tools and the formula when investigating. It never computes a number and never sets the final label |

Option B as in Phase 1: the backend owns data, tools and math; the AI service runs the model and calls the backend's read-only tools.

---

## 3. Core concepts

| Concept | Meaning |
|---|---|
| **Cost Memory** | What Vero expects each recurring cost to be: from the statement (Phase 1's recurring detection on the rows before `as_of`) and from the documents (lease, contracts, price lists, previous invoices) |
| **Billing document** | Asks for money: invoice, rent adjustment. Uploading one starts a check |
| **Evidence document** | Sets or explains amounts: lease, contract, terms, price list, notice. Stored, searched, cited; never a trigger by itself |
| **Difference** | A billed amount or unit price that doesn't match Cost Memory (§5.3) |
| **Known reason** | A stored price list that gives exactly the new price. CHECK can use it without the model. CHECK **never interprets clauses** |
| **Finding** | One difference and everything Vero did about it: classification, trace, impact, evidence, propagation, actions. Schema name `ProofFinding` (Phase 1 already has a `Finding`: the overview's ANALYSED facts) |
| **Routine check** | A recurring payment compared with its contract and found matching. Shown in the digest, not counted as a change |
| **Proof run** | What an upload starts: extraction → CHECK → investigation of each unexplained finding → done. Run kind `PROOF`, next to Phase 1's `CASH` |
| **Investigation** | One agent, one finding, at most 6 read-only tool calls, 60 s |

### Conventions

| Kind | Rule | Example |
|---|---|---|
| Money | integer cents as a string (as in Phase 1) | 4.104,00 € → `"410400"`; 1.234,56 € → `"123456"` |
| Percentages | basis points | +8 % = `800`; VAT 19 % = `1900`, 7 % = `700` |
| Index values | tenths of a point | VPI 121.2 = `1212` |
| Unit prices, quantities | `Decimal`: a decimal string, never a float | `"0.1234"` €/kWh, `"2.5"` kg |
| Dates | `YYYY-MM-DD` | 01.06.2025 → `"2025-06-01"` |
| Boxes | `[x0, y0, x1, y1]` in PDF points, origin top-left | |

### Classifications

| Shown to the owner | Internal | Meaning | Vero's behaviour |
|---|---|---|---|
| ✅ **Matches** | `verified` | Fully explained by evidence | No alert; a digest row |
| 🔍 **Needs evidence** | `unexplained` | Not enough evidence yet | Starts an investigation; stays only if the investigation can't resolve it ("escalated", with what was searched) |
| ⚠️ **Conflicts with contract** | `conflict` | The evidence contradicts the billed amount | Alert with side-by-side evidence, justified and disputed amounts, prepared actions |

---

## 4. The data

### 4.1 The rule
**The organizer's CSV is never edited.** Phase 1's verified numbers and the organizer's published balances depend on it. Every synthetic document must agree with it:
- every invoice total equals a real payment (same counterparty, date, amount, invoice number in the payment reference); an invoice is dated on the day it was paid;
- every outcome matches what the statement shows afterwards. The hero case recommends keeping the rent at 3 800,00 €, and the statement shows 3 800,00 € all year.

The CSV itself shows no change in any recurring amount during 2025 (rent 3 800, utilities 650, CHF tranche 4 166,67 CHF, social security 2 650, wage tax 880). Only the CHF tranche's euro amount moves, with the rate written in each row.

### 4.2 Documents

All synthetic, in German with an English translation, generated from one fixture file (P02) into PDFs with a real text layer (P03). Boxes are stored in PDF points with the origin top-left. Each document says where it lives: the owner's documents (listed from the start), the mailbox (the landlord's letter, found only by searching the inbox) or the upload (the rent adjustment).

| Document | ID | Content |
|---|---|---|
| Commercial lease | `lease_main_street_2021` | Main Street Real Estate GmbH, Frankfurt. Signed 15 Jan 2021, fixed term 10 years from 1 Feb 2021. A consolidated version (*Lesefassung*) including addendum no. 1 of 25 Jan 2024, so it states the current rent without an original 2021 rent: 3 800,00 €/month (§4.1), due on the 1st, since the last adjustment on 1 Feb 2024 (reference index Jan 2024). **§4.2** index clause; §4.3: the rent includes all operating costs except electricity and gas (gross rent), so higher operating costs can raise it only under §4.2 |
| Landlord's letter | `ntc_main_street_2025_04` | PDF attached to an email of 3 Apr 2025 in a thread about the **courtyard grease trap maintenance**: "Erhöhung der Miete um 8 % ab 01.06.2025 aufgrund gestiegener Bewirtschaftungskosten." No reference to §4.2 or the index. Found only by `search_inbox` |
| Rent adjustment (**the live upload**) | `doc_rent_adjustment_2025_06` | Dated 30 Apr 2025. Prints "bisher 3.800,00 €" and "neu 4.104,00 €" monthly from 1 Jun 2025. No reasoning |
| Silent partnership agreement | `ctr_luigi_2024` | Luigi Amalfi (Luigi's Ristorante Zurich): 50 000 CHF in 12 monthly tranches of 4 166,67 CHF on the 15th, converted by the bank at the day's rate |
| Utilities payment plan | `ctr_city_energy_2025` | City Energy & Utilities AG: monthly advance 650,00 € |
| Fattoria price list 2025 | `pl_fattoria_2025` | Wine and beverage prices valid from 1 Apr 2025, some items up from 2024 |
| Fattoria invoices | `inv_FLV-16261`, `inv_FLV-94818` | 7 Mar (2024 prices) and 28 Apr (2025 prices) |
| Asimex price list Q2 | `pl_asimex_2025_q2` | Food and ingredient prices valid from 1 Apr 2025, some items up |
| Asimex terms | `terms_asimex_2025` | "Logistikzuschlag" 24,50 € net per delivery from 1 May 2025 for deliveries under 600,00 € net goods value |
| Asimex announcement | `mail_asimex_2025_04_14` | Email of 14 Apr 2025 announcing the surcharge and the new terms. Found only by `search_inbox` |
| Asimex invoices | `inv_AS-80497`, `inv_AS-72217`, `inv_AS-11607` | 8 Mar (old prices), 26 Apr (Q2 prices), 3 May (Q2 prices + surcharge) |
| Mailbox | `mail_*` | ~20 emails: the grease-trap thread, suppliers, Techniker Krankenkasse, tax office, Luigi, staff rota. The search must visibly find the right one |

### 4.3 The lease clause and the terms, with their structured parameters

Every clause carries its terms as parameters next to its text, so the backend can check what the agent cites (§5.6).

| Clause | Text (summary) | Parameters |
|---|---|---|
| Lease §4.2 | The rent follows the Destatis consumer price index (VPI, 2020 = 100). An adjustment is allowed only if the index has changed by at least 5 % since the last adjustment, on written request, from the month after the request, at most once in 12 months | `{index: "DESTATIS_VPI", base_period: "2024-01", threshold_bps: 500, last_adjustment: "2024-02-01", min_months_between: 12, written_request: true}` |
| Asimex terms | Surcharge per delivery under a net goods value | `{surcharge: 2450, below_net_goods: 60000, valid_from: "2025-05-01"}` |

### 4.4 Reference values (demo values, labelled as such in the UI)

| ID | Values |
|---|---|
| `ref_vpi_demo` | Jan 2024: 117.6 (`1176`); Mar 2025: 121.2 (`1212`). Only these two months, the ones the demo needs; no values in between are invented. To check against the official Destatis series before the pitch |
| `ref_eurchf_demo` | The rates in the statement rows: Jan 1.042, Feb 1.045, Mar 1.038, Apr 1.040 |

### 4.5 Invoice totals (all VAT rounded on the totals, per rate)

| Invoice | Paid | Net 7 % | VAT 7 % | Net 19 % | VAT 19 % | Gross |
|---|---|---|---|---|---|---|
| FLV-16261 | 7 Mar | 3000 | 210 | 84463 | 16048 | **103721** |
| FLV-94818 | 28 Apr | | | 75385 | 14323 | **89708** |
| AS-80497 | 8 Mar | 54649 | 3825 | | | **58474** |
| AS-72217 | 26 Apr | 64227 | 4496 | | | **68723** |
| AS-11607 | 3 May | 58321 (goods 55871 + surcharge 2450) | 4082 | | | **62403** |

FLV-16261 has no single-rate solution at 19 %, so it carries one 7 % food line (e.g. olive oil). The line items inside each total are set in the fixture (P02).

---

## 5. Flow

### 5.1 The loop
```
SENSE ──► CHECK ──► INVESTIGATE ──► PROPAGATE ──► ACT
```

| Step | What happens | Who |
|---|---|---|
| **SENSE** | An uploaded PDF is read by the model; preloaded documents use their fixture values | AI service + backend validation |
| **CHECK** | Line items and recurring payments against Cost Memory → no finding, `verified`, or `unexplained` | Backend, deterministic |
| **INVESTIGATE** | Only for `unexplained`: one agent searches for evidence and proposes a label and a formula | AI service through backend tools |
| **PROPAGATE** | Payment decision, effect on the Vero Cash forecast, disputed amounts | Backend |
| **ACT** | Prepared actions through the policy lanes; approval; simulated execution | Backend (+ letter text from the AI) |

### 5.2 Proof run and finding states
```
upload → EXTRACTING → CHECKING ─┬─► no difference ............................ done
                                ├─► verified (known reason) ................. done (digest)
                                └─► unexplained → INVESTIGATING → classified ─► PENDING_APPROVAL → RESOLVED
```
Finding status: `INGESTED → EXTRACTED → CHECKED → (INVESTIGATING) → CLASSIFIED → PENDING_APPROVAL → RESOLVED`. Every transition is a trace step. One run at a time across `CASH` and `PROOF`: an upload while a run is in progress gets 409 `RUN_IN_PROGRESS` before anything is stored. Uploaded PDFs (at most 5 MB) are stored behind the repository (PostgreSQL `bytea`), not on disk: the API is a single replica with no volume.

### 5.3 SENSE: extraction (agreed with the AI team)
- The backend sends `{document_id, filename, content_base64, known_counterparties}` to `POST ai/v1/documents/extract` (internal token, 30 s).
- The AI service reads the text layer (`pypdfium2`). The model returns, per field, the printed text and its line; code finds that text, takes its box from the PDF and parses the value. The model never writes a number or a box, so a value always equals its printed text, and an instruction hidden in a document can't create a value that isn't printed.
- The AI service checks the arithmetic and gives the model one correction round.
- **The backend checks everything again and decides:** each value re-parsed from its printed text; net per line = quantity × unit price (half-up); VAT per rate on the totals ±1 cent; gross = net + VAT on the totals; the counterparty match supported by the printed name; the date not after `as_of`.
- **On failure:** a demo document falls back to its fixture values (run `FALLBACK`, reason in the trace); any other document ends `EXTRACTION_FAILED`. The stage demo uses the live extraction.
- A rent adjustment is extracted as fields (`old_amount`, `new_amount`, `effective_from`, `frequency`), not as a line.
- Unreadable input gets 422 with a code (`NO_TEXT_LAYER` and the others in `services/ai/src/ai/extraction.py`; limits 15 MB, 20 pages). Implemented in the AI service (PR #23).
- The AI team's live test (`test_extract_live.py`) runs on our demo PDFs with the `expected.json` the PDF generator writes (P03).

### 5.4 CHECK: what counts as a difference
- **Fixed recurring costs** (rent, utilities, CHF tranche): the billed amount against Cost Memory, ±2 % as in Phase 1's recurring detection; the CHF tranche in CHF. For a rent adjustment, `new_amount` from `effective_from` is the billed amount.
- **Supplier invoices:** unit price per line against the **previous invoice** of the same supplier (the difference); then the price list valid on the invoice date (the known reason → `verified`). A goods line never seen before is explained if it is priced as in the valid price list; a line on no price list (the surcharge) is a difference. Preloaded invoices are checked in date order, so AS-11607 is compared with AS-72217.
- **Routine checks:** each statement payment of a recurring item with a contract behind it is compared with the contract amount. A match is a routine check; a mismatch is a difference.
- CHECK never reads a clause. Applying a clause is the investigation's job; otherwise the backend would settle the hero case alone.

### 5.5 INVESTIGATE: hypotheses and tools
Hypotheses, in this order for the scripted fallback (the agent may choose its own): **H1** a notice or letter gives a basis · **H2** a contract clause allows it · **H3** a reference value or price list explains it · **H4** quantity or scope changed.

| Tool (read-only) | Returns |
|---|---|
| `search_inbox(query, counterparty, date_range)` | Matching emails and attachments (keyword search shared with the RAG assistant, German aliases) |
| `search_documents(query, doc_type)` | Matching leases, contracts, terms, price lists |
| `read_clause(doc_id, section)` | Clause text, page, box and structured parameters |
| `get_reference_index(name, periods)` | VPI or EUR/CHF values, flagged as demo values |
| `get_price_list(supplier, item, date)` | The list price valid on that date |
| `compute(formula_id, inputs)` | A formula from the engine (§5.7) |

Email and document text is untrusted data: returned as data, never followed as an instruction. At most 6 calls; a 7th gets `TOOL_LIMIT_REACHED`.

### 5.6 Grounding and the final label (backend)
- A citation no tool returned in this run is dropped and noted in the trace.
- The completion names its `compute` call; every input of that call must come from Cost Memory or an earlier tool result in the run (amounts from Cost Memory or the billing document, index values from `get_reference_index`, clause terms from `read_clause`'s parameters). An input with no source makes the result unusable → escalated.
- **Final label, set by the backend:** billed above the justified amount → `conflict`; billed equal to it → `verified`; no usable evidence → `unexplained` (escalated) with the search list.
- If the agent fails or misses its deadline, the scripted investigation runs with the same tools (`FALLBACK`).

### 5.7 Formulas (deterministic, integer cents, half-up)

| Formula | Rule |
|---|---|
| `variance` | delta = billed − previous; bps = delta × 10000 / previous |
| `vpi_threshold_indexation` | change_bps = (current − base) × 10000 / base; below `threshold_bps` → justified = base amount; otherwise justified = base amount × current / base |
| `fx_conversion` | CHF cents × rate, half-up (Phase 1's `money.convert`) |
| `price_list_check` | the unit price equals the list price valid on the date |
| `surcharge_rule` | net goods value below `below_net_goods` on or after `valid_from` → `surcharge`, else 0 |
| `annualize` | monthly × 12 |
| `window_impact` | the Phase 1 forecast run twice (billed vs. justified); the difference in outflows inside the 91-day window and in the lowest balance |

### 5.8 PROPAGATE and ACT
- **Payment decision:** keep the current amount (conflict with justified = current) or propose the justified amount from `effective_from`.
- **Forecast:** `window_impact`; an executed amount change updates the future outflows of that recurring item and re-runs the Vero Cash forecast.
- **Actions:** `KEEP_PAYMENT`, `UPDATE_RECURRING_AMOUNT`, `SEND_LETTER` (German, neutral, citing the clause).

---

## 6. Business rules

- **Every Proof action needs approval** (`NEEDS_APPROVAL`): letters contact a third party, amount changes move money. Amount changes on protected categories (`PAYROLL`, `SOCIAL_SECURITY`, `TAX`) are `NOT_ALLOWED`. A paused mandate is respected.
- **Never withhold rent.** Vero recommends paying the contractual rent and asking in writing for the basis of the increase; arrears would put the lease at risk.
- **Only `execute_move`, `execute_credit` and the new `execute_amount_change` change the schedule.** An outflow keeps its original amount, and the backtest restores it as it restores dates: simulated amount changes never change the backtest.
- An executed amount change bumps `financial_version`; a pending Phase 1 recommendation then becomes `STALE_RECOMMENDATION`. Demo order: Vero Proof first, then the closure story.
- **The digest** lists the findings the owner hasn't acknowledged or decided yet and the latest routine checks. A quiet finding leaves it with one tap (`POST /findings/{id}/acknowledge`); a finding with actions leaves it when its actions are decided. No date window: the demo clock is fixed at 5 May; "weekly" is only the label.
- Supplier communication is in German (English available). Language is neutral and evidence-based, never accusatory.

---

## 7. Reference demo scenario (all teams test against these numbers)

### 7.1 Hero: the rent (⚠️ conflict, live upload)

| Quantity | Value (cents) |
|---|---|
| previous rent (statement + lease) | 380000 |
| billed from 1 Jun 2025 | 410400 |
| variance | 30400 (800 bps) |
| VPI base (Jan 2024) → current (Mar 2025) | 1176 → 1212 |
| index change | (1212 − 1176) × 10000 / 1176 = 306.12 → **306 bps** < 500 → no adjustment allowed |
| justified rent | **380000** |
| disputed | **30400/month**, **364800/year** (assumes the billed rent continues for 12 months) |
| window impact | **91200**: rent on 1 Jun, 1 Jul, 1 Aug 2025 inside the 5 May – 3 Aug window |
| propagation | keep `RENT` at 380000; Phase 1 S0 unchanged |
| actions | `KEEP_PAYMENT`, `SEND_LETTER` (subject: "Ihre Mietanpassung ab 01.06.2025 – Bitte um Erläuterung gemäß §4.2 Mietvertrag") |

Expected trace (4 tool calls): `search_inbox` → the landlord's letter in the grease-trap thread · `read_clause(lease, §4.2)` · `get_reference_index(DESTATIS_VPI, 2024-01, 2025-03)` → 1176, 1212 · `compute(vpi_threshold_indexation)` → justified 380000.

### 7.2 Quiet changes (✅, preloaded, digest)

| Case | Difference | Resolved by |
|---|---|---|
| Fattoria FLV-94818 (89708) | unit prices vs FLV-16261 (103721) | CHECK: `pl_fattoria_2025` |
| Asimex AS-72217 (68723) | unit prices vs AS-80497 (58474) | CHECK: `pl_asimex_2025_q2` |
| Asimex AS-11607 (62403) | new line "Logistikzuschlag" 2450 | **Investigation** (scripted at reset): `search_inbox` → announcement of 14 Apr · `read_clause(terms_asimex_2025)` · `compute(surcharge_rule)`: goods 55871 < 60000 → 2450 → `verified` |

### 7.3 Routine checks (digest, not changes)

The digest shows the latest payment of each item before `as_of`.

| Payment | Contract | Result |
|---|---|---|
| Rent 380000 (1 May) | lease | matches |
| Utilities 65000 (10 Apr) | payment plan | matches |
| CHF tranche 416667 CHF (15 Apr; 433334 EUR @ 1.040; Mar 432500 @ 1.038) | partnership agreement | matches (compared in CHF) |

### 7.4 Digest counts

| Moment | Changes | Handled quietly | Need the owner | Routine checks |
|---|---|---|---|---|
| After reset | 3 | 3 | 0 | 3 |
| After the hero upload | 4 | 3 | **1** | 3 |

### 7.5 Variants (tests; optional demo switches)

| Variant | Change | Result |
|---|---|---|
| **VPI above the threshold** | VPI current 1236 instead of 1212 | change 510 bps ≥ 500 → justified 380000 × 1236 / 1176 = **399388**; still `conflict` (billed 410400): disputed 11012/month, 132144/year; `UPDATE_RECURRING_AMOUNT` to 399388 for `RENT-06…08` changes the forecast; backtest unchanged |
| **Escalated surcharge** | no `mail_asimex_2025_04_14` in the fixture | AS-11607 ends `unexplained` (escalated) with the list of what was searched |

---

## 8. Domain notes (Germany)

Our understanding, **not legal advice**; to be checked by someone who knows German commercial leases (e.g. a Commerzbank mentor) before the dataset is locked.

- Commercial leases often contain an index clause (*Wertsicherungsklausel*) tied to the Destatis VPI. The Price Clause Act (*Preisklauselgesetz*) allows them under conditions such as a long fixed term; our lease has 10 years.
- Many clauses allow an adjustment only after a threshold move and on written request. "Rising operating costs" is not a basis under such a clause. Operating costs are either settled separately (advance payments and a yearly *Nebenkostenabrechnung*) or included in a gross rent (*Bruttomiete*). Our lease uses a gross rent (§4.3), because the statement shows no operating-cost payments to the landlord: higher operating costs are the landlord's risk unless §4.2 allows an adjustment.
- Utilities are paid as monthly advances (*Abschlag*) with a yearly settlement (*Jahresabrechnung*).
- Supplier invoices carry 7 % (food) or 19 % (wine, beverages) VAT, usually rounded per rate on the totals.

---

## 9. Ownership and open decisions

| Area | Owner |
|---|---|
| Fixture, PDF generator, Cost Memory, CHECK, engine, tools, Proof runs, grounding, propagation, policy, digest, contracts | Backend |
| Extraction endpoint, investigation loop and prompt (`submit_finding`), letter text, `test_extract_live.py` | AI |
| Upload with progress, live trace, side-by-side highlighted PDFs, action cards, digest | Frontend |
| Lease clause and German wording review | Mentor |

**Decided**
- Pizzeria Amalfi, the CSV unchanged, synthetic documents that agree with it.
- Detection is deterministic; one agent per unexplained finding; the backend sets the final label.
- Uploaded PDFs are read by the model, with values pointing at printed text; the backend validates; fixture fallback for demo documents.
- Hero outcome: the whole increase disputed (the VPI below the threshold).
- Money in cents, percentages in basis points, unit prices and quantities as `Decimal` strings.

- Extraction implemented by the AI team as agreed (PR #23), including its 422 codes.
- The investigation tools reuse the RAG assistant's keyword search (`api/rag.py`), split into a shared ranking function.

**Still to agree**
1. The investigation contract with the AI team: start payload, tool list per run (`GET /tools?run_id=…`), the `submit_finding` completion.
2. The RAG assistant's field renames (`amount_cents` → `amount`, `UPPER_SNAKE` kinds), which the browser reads.
3. The VPI values and the domain notes (§8), checked before the pitch.
