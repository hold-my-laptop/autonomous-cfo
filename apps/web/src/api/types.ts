/**
 * Frontend data shapes. The real backend contract is docs/contracts/openapi.json (services/api);
 * in live mode adapters.ts maps it onto these types (see the mapping at the bottom of this file).
 * CFO types use camelCase JSON; the Vero cost-check types (Finding & co.) use snake_case on purpose,
 * they mirror the agent's Finding schema one-to-one.
 * Money: plain numbers in EUR. Dates: ISO 8601 strings.
 */

// ───────────────────────── Common ─────────────────────────

export type ISODate = string;      // "2025-04-14"
export type ISODateTime = string;  // "2025-04-14T07:15:00Z"
export type Eur = number;

/**
 * Which agent produced an item.
 *  liquidity = Cash agent, fx = Currency agent, tax = Tax agent,
 *  costs = Vero (cost check), contract = Contract check.
 */
export type AgentId = "liquidity" | "fx" | "tax" | "costs" | "contract";

/** Status of a forecast week: green = above the safety buffer, yellow = below the buffer, red = below zero (overdraft). */
export type RiskLevel = "green" | "yellow" | "red";

/**
 * The four things the challenge asks us to make visible:
 *  - analysis:          the agent looked at the data on its own (no action)
 *  - recommendation:    the agent suggests something; the owner may ignore it
 *  - approval_required: nothing happens until the owner approves
 *  - done:              the agent acted on its own, inside the owner's limits
 */
export type AutonomyLevel = "analysis" | "recommendation" | "approval_required" | "done";

/** Whether an action is only simulated (demo) or really allowed to run. */
export type ExecutionMode = "simulated" | "permitted";

/**
 * Which model produced an item and where it ran. Shown as one quiet line:
 * "Granite 3.3 · vLLM on OpenShift AI · trace abc123 · guardrails passed".
 */
export interface ModelInfo {
  name: string;              // "granite-3.3-8b-instruct"
  servedBy: string;          // "vLLM on Red Hat OpenShift AI"
  traceId?: string;          // MLflow trace id
  guardrails?: "passed" | "blocked";
}

// ───────────────────────── Company & policy ─────────────────────────

export interface Company {
  id: string;
  name: string;              // "Pizzeria Amalfi"
  /** Mock only: the real backend does not send these three. */
  iban?: string;
  city?: string;             // "Frankfurt am Main"
  employees?: number;
  ownerFirstName: string;    // "Vincenzo"
  currency: "EUR";
  /** "Today" in the demo time machine. Agents only see transactions before this date. */
  asOf: ISODate;             // "2025-04-14"
  cashTodayEur: Eur;         // balance at the end of the day before asOf
  safetyBufferEur: Eur;      // minimum cash the owner wants to keep
}

/** Limits the owner sets. The agent only acts alone inside these. */
export interface Policy {
  autoApproveMaxEur: Eur;              // e.g. 1000
  canMoveFlexiblePayments: boolean;    // within due dates
  protectedCategories: PaymentCategory[]; // never moved: salary, social security, tax
  executionMode: ExecutionMode;        // "simulated" for the hackathon
}

// ───────────────────────── Decisions (inbox items) ─────────────────────────

/** One step of the business logic, shown under "How did you decide?". */
export interface ReasoningStep {
  kind: "analysis" | "rule" | "calculation" | "recommendation";
  title: string;             // "June shutdown: no revenue"
  detail: string;            // "Zero card sales from 1 to 30 June"
  passed?: boolean;          // for rules: true = OK, false = problem found
}

/** A key number shown on the card. */
export interface Fact {
  label: string;             // "Lowest balance"
  value: string;             // already formatted by backend: "−€22,098"
  tone?: "neutral" | "good" | "bad";
}

/** A bank product the agent proposes (overdraft, FX forward, ...). */
export interface ProductOffer {
  kind: "credit_line" | "overdraft" | "fx_forward" | "fixed_deposit";
  name: string;              // "Overdraft facility"
  amountEur: Eur;
  termWeeks: number;
  ratePct: number;           // yearly rate (or the forward rate for fx_forward)
  costEur?: Eur;             // interest to pay
  incomeEur?: Eur;           // interest earned
  note?: string;             // "Example rate for the demo"
}

export interface DraftMessage {
  kind: "payment_reminder" | "counter_proposal" | "bank_request" | "partner_request" | "tax_office_request";
  to: string;
  subject: string;
  body: string;
  /** Same message in English, when the original is in another language. */
  english?: { subject: string; body: string };
}

export type DecisionStatus =
  | "pending"    // waiting for the owner
  | "approved"   // owner said yes
  | "rejected"   // owner said no / not now
  | "done"       // agent did it alone (autonomy = done)
  | "info";      // analysis only, nothing to decide

export type DecisionAction = "approve" | "reject";

export interface Decision {
  id: string;
  agent: AgentId;
  autonomy: AutonomyLevel;
  execution: ExecutionMode;
  status: DecisionStatus;
  createdAt: ISODateTime;

  headline: string;          // plain language: "Arrange a €25,000 overdraft facility before 1 June"
  summary: string;           // one or two sentences: why
  amountEur?: Eur;
  facts: Fact[];             // 2–3 key numbers
  reasoning: ReasoningStep[];// business logic, step by step

  /** Button texts — specific verbs, e.g. "Request overdraft" / "Not now". */
  approveLabel?: string;
  rejectLabel?: string;
  /** Text shown after the owner acts. */
  approvedMessage?: string;
  rejectedMessage?: string;

  offer?: ProductOffer;
  draftMessage?: DraftMessage;

  // Two checks: LLM proposes, rule engine verifies
  llmVerdict?: "approve" | "hold" | "review";
  ruleVerdict?: "approve" | "hold" | "review";
  agreed?: boolean;
  model?: ModelInfo;

  resolvedAt?: ISODateTime;
  resolvedBy?: "agent" | "owner";
  relatedIds?: string[];
}

// ───────────────────────── Liquidity: cash forecast ─────────────────────────

export type PaymentCategory =
  | "salary"
  | "social_security"
  | "tax"
  | "supplier"
  | "rent"
  | "utilities"
  | "fx_transfer"
  | "loan"
  | "other";

export interface ScheduledPayment {
  id: string;
  counterparty: string;
  category: PaymentCategory;
  amountEur: Eur;
  currency: "EUR" | "CHF";
  dueDate: ISODate;
  scheduledDate: ISODate;
  flexible: boolean;
  movedByAgent?: boolean;
}

export interface ForecastWeek {
  week: number;              // 1..13
  startDate: ISODate;        // Monday
  inflowEur: Eur;
  outflowEur: Eur;
  balanceEur: Eur;           // closing balance of the week (actual if isActual)
  lowestEur?: Eur;           // lowest daily balance inside the week
  bufferEur: Eur;
  status: RiskLevel;
  /** After the demo date moved past this week: balanceEur is the real balance. */
  isActual?: boolean;
  /** What the forecast made on Forecast.madeOn said for this week (only once actuals exist). */
  forecastEur?: Eur;
}

/** One day of the 91-day forecast: closing balance, and from "today" on the likely range around it. */
export interface ForecastDay {
  date: ISODate;
  balanceEur: Eur;           // closing balance (actual before the demo date, forecast from it on)
  lowEur?: Eur;              // likely range, only for forecast days
  highEur?: Eur;
}

export interface Forecast {
  generatedAt: ISODateTime;
  madeOn: ISODate;           // the demo date the forecast was computed on
  horizonDays: number;       // 91
  startingBalanceEur: Eur;
  weeks: ForecastWeek[];     // 13 entries
  days: ForecastDay[];       // 91 entries, one per day, same horizon as `weeks`
  firstShortDate?: ISODate;  // first week that closes below zero, if any
  lowestBalanceEur: Eur;     // lowest daily balance in the horizon
  lowestDate: ISODate;
  lowestWeek: number;
  spareCashEur: Eur;         // money not needed in the horizon
  /** Arranged overdraft facility (simulated); weeks are then judged on balance + limit. */
  overdraftLimitEur?: Eur;
  /** Only once actuals are known (after "Fast-forward"). */
  accuracy?: { weeksCompared: number; meanAbsErrorEur: Eur; meanAbsErrorPct: number };
}

// ───────────────────────── Contract check ─────────────────────────

export interface ContractTerms {
  counterparty: string;
  valueEur: Eur;
  advancePct: number;
  paymentTermDays: number;
  startDate: ISODate;
  durationWeeks: number;
  costEur: Eur;              // food, staff and material costs the owner pays first
}

export interface LeaseTerms {
  landlord: string;
  monthlyRentEur: Eur;
  indexClause: string;       // "§5 Indexmiete: rent follows the German consumer price index (VPI)"
  noticeRules: string;
  startDate: ISODate;
}

interface ContractCheckBase {
  contractId: string;
  fileName: string;
  /** Registered in the document store; Vero can cite it. */
  documentId: string;
  /** How many open cost checks were (re)started because of this document. */
  costChecksTriggered: number;
  model?: ModelInfo;
}

/** A customer or supplier contract, tested on the 13-week forecast. */
export interface ContractSimulation extends ContractCheckBase {
  kind: "contract";
  extractedTerms: ContractTerms;
  baseline: ForecastWeek[];
  withContract: ForecastWeek[];
  withSuggestion: ForecastWeek[];
  valleyWeek: number;
  minBalanceEur: Eur;
  extraOverdraftEur: Eur;    // how much deeper the lowest point gets
  suggestion: string;
  suggestedTerms: Partial<ContractTerms>;
  counterProposal: DraftMessage;
}

/** A lease: its terms are registered so Vero can check rent changes against it. */
export interface LeaseCheck extends ContractCheckBase {
  kind: "lease";
  lease: LeaseTerms;
}

export type ContractCheckResult = ContractSimulation | LeaseCheck;

// ───────────────────────── Activity log (audit) ─────────────────────────

export type AuditActor = "agent" | "rules" | "owner" | "system";

/**
 * Append-only, hash-chained entry.
 * hash = sha256(`${prevHash}|${seq}|${timestamp}|${actor}|${action}|${decisionId ?? ""}`)
 * First entry: prevHash = "0".repeat(64).
 */
export interface AuditEntry {
  seq: number;
  timestamp: ISODateTime;
  actor: AuditActor;
  agent?: AgentId;
  autonomy?: AutonomyLevel;
  execution?: ExecutionMode;
  action: string;
  decisionId?: string;
  model?: ModelInfo;
  prevHash: string;
  hash: string;
}

// ───────────────────────── Vero: cost check (snake_case) ─────────────────────────

/** Internal state → user label: verified = Matches, unexplained = Needs evidence, conflict = Conflicts with contract. */
export type Classification = "verified" | "unexplained" | "conflict" | "escalated";

export type FindingStatus =
  | "ingested"
  | "extracted"
  | "checked"
  | "investigating"
  | "classified"
  | "pending_approval"
  | "resolved";

export interface LineItem {
  description: string;
  previous_value: number;
  current_value: number;
  currency: "EUR";
  unit: string;
  effective_from: ISODate;
  monthly_variance: number;
  variance_pct: number;
}

export type ToolName =
  | "read_document"
  | "search_inbox"
  | "search_documents"
  | "read_clause"
  | "get_reference_index"
  | "compute";

export interface TraceStep {
  step: number;
  hypothesis: string;
  tool: ToolName;
  input: Record<string, string | number>;
  result: string;
  evidence_ref: string | null;
  model?: ModelInfo;
}

export interface FinancialImpact {
  justified_value: number;
  justified_increase_monthly: number;
  disputed_amount_monthly: number;
  disputed_amount_annual: number;
  assumption: string;
}

export interface EvidenceRef {
  type: "statement" | "notice" | "lease" | "contract" | "invoice" | "reference";
  doc_id: string;
  page?: number;
  clause?: string;
  snippet: string;
}

export type Propagation =
  | { target: "standing_order"; id: string; old: number; new: number; note: string }
  | { target: "forecast"; id: string; delta_monthly: number; from: ISODate; note: string };

export interface RecommendedAction {
  type: "send_clarification_letter" | "update_standing_order" | "update_forecast";
  title: string;
  detail: string;
  requires_approval: true;
  execution: "simulated";
  language?: "de" | "en";
  draft?: DraftMessage;
}

export interface Finding {
  finding_id: string;
  status: FindingStatus;
  classification: Classification;
  classification_history: Classification[];
  vendor: string;
  billing_period: string;
  /** The document that triggered the check (notice, invoice, ...). */
  source_doc_id: string;
  line_item: LineItem;
  investigation_trace: TraceStep[];
  financial_impact?: FinancialImpact;
  evidence_refs: EvidenceRef[];
  propagation: Propagation[];
  recommended_actions: RecommendedAction[];
  /** Set when Vero cannot finish without more evidence from the owner. */
  evidence_request?: { message: string; action_label: string; href: string };
  created_at: ISODateTime;
  resolved_at?: ISODateTime;
}

/** A document Vero can cite. */
export interface KnownDocument {
  doc_id: string;
  kind: "invoice" | "contract" | "lease" | "notice" | "statement";
  label: string;        // "Lease, Main Street Real Estate"
  note: string;         // "§5 index rent (CPI)"
  source: "history" | "contract_check" | "inbox";
  added_at: ISODateTime;
}

/** One line of a source document. `box` draws a highlight around it. */
export interface DocLine {
  text: string;
  /** English gloss shown under the line. */
  en?: string;
  box?: { label: string; tone: "billed" | "basis" | "neutral" };
  muted?: boolean;
  strong?: boolean;
}

export interface SourceDoc {
  doc_id: string;
  kind: "Statement" | "Notice" | "Lease" | "Contract" | "Invoice";
  title: string;
  meta: string;
  page: number;
  lines: DocLine[];
}

/** A recurring cost that changed and was explained without bothering the owner. */
export interface DigestRow {
  id: string;
  classification: Classification;
  title: string;
  detail: string;
  doc_id: string;
  calc?: string[];
}

export interface CostMemoryRow {
  label: string;
  vendor: string;
  months: { period: string; value: number; partial?: boolean }[];
}

// ───────────────────────── Live events (SSE: GET /api/v1/events) ─────────────────────────

export type ServerEvent =
  | { type: "decision.created"; data: Decision }
  | { type: "decision.updated"; data: Decision }
  | { type: "forecast.updated"; data: Forecast }
  | { type: "company.updated"; data: Company }
  | { type: "audit.appended"; data: AuditEntry }
  | { type: "agent.status"; data: { agent: AgentId | "system"; message: string; busy: boolean } }
  | { type: "finding.updated"; data: Finding }
  | { type: "finding.step"; data: { finding_id: string; step: TraceStep } }
  | { type: "document.added"; data: KnownDocument };

// ───────────────────────── Demo controls ─────────────────────────

export type DemoScenario =
  | "reset"         // calm Monday 14 April 2025
  | "closure"       // live only: owner plans to close 1–30 June → POST /events CLOSURE_PLANNED
  | "rent_notice"   // mock only: landlord announces +8% rent from 1 June → Vero investigates
  | "fast_forward"; // mock only: jump to 1 July 2025: reveal actuals, forecast vs actual

// ───────────────────────── Chat (AI service, POST /api/v1/chat via nginx) ─────────────────────────

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatRequest {
  question: string;          // 1–500 chars
  language?: "en" | "de";
  history?: ChatTurn[];      // at most 6 turns
}

export interface ChatResponse {
  answer: string;
  source: "MODEL" | "FALLBACK";
  tools_used: string[];
  fallback_reason?: string | null;
}

// ───────────────────────── Live mode: how these map to the real backend ─────────────────────────
//
// company   ← GET /company + GET /mandate (cash_floor → safetyBufferEur)
// policy    ← GET /mandate
// decisions ← GET /overview (all lanes; ANALYSED findings become analysis-only items)
// act       → POST /recommendations/{id}/approve|reject (Idempotency-Key)
// forecast  ← GET /forecast?weekly=true
// live      ← polls GET /runs?limit=1 (the backend has no event stream)
// demo      → POST /demo/reset, POST /events (CLOSURE_PLANNED)
// chat      → POST /chat (AI service)
// Not in the backend yet (Phase 2 or undecided): audit, contracts, costs/*, documents/{id}.
// The mapping lives in adapters.ts; the wire shapes in backend.ts.
//
// ───────────────────────── Mock API endpoints (all under /api/v1) ─────────────────────────
//
// GET  /api/v1/company                              → Company
// GET  /api/v1/policy                               → Policy
// GET  /api/v1/decisions                            → Decision[]
// POST /api/v1/decisions/{id}/approve               → Decision
// POST /api/v1/decisions/{id}/reject                → Decision
// GET  /api/v1/forecast                             → Forecast
// GET  /api/v1/payments                             → ScheduledPayment[]
// POST /api/v1/contracts (multipart)                → ContractCheckResult
// GET  /api/v1/audit                                → AuditEntry[]
// GET  /api/v1/costs/findings                       → Finding[]
// GET  /api/v1/costs/memory                         → CostMemoryRow[]
// GET  /api/v1/costs/documents                      → KnownDocument[]
// GET  /api/v1/costs/digest                         → DigestRow[]
// GET  /api/v1/documents/{doc_id}                   → SourceDoc
// POST /api/v1/costs/findings/{finding_id}/approve  → Finding   (body: { actions: RecommendedAction["type"][] })
// GET  /api/v1/events                               → text/event-stream of ServerEvent
// POST /api/v1/demo/{scenario}                      → { ok: true }
