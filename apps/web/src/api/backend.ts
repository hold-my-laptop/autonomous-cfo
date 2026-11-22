/**
 * Wire shapes of the real backend (services/api, docs/contracts/openapi.json), only the parts the UI reads.
 * Money is a string of integer cents ("123456" = €1,234.56); dates are ISO strings.
 * adapters.ts turns these into the UI types in types.ts.
 */

export type Cents = string;

export type BeCategory =
  | "CARD_REVENUE"
  | "VARIABLE_COST"
  | "RENT"
  | "PAYROLL"
  | "SOCIAL_SECURITY"
  | "TAX"
  | "UTILITIES"
  | "PARTNER_TRANSFER"
  | "OTHER";

export interface BeCompany {
  name: string;
  owner: string;
  currency: string;
  as_of_date: string;
  balance: Cents;
  statement_until: string;
  financial_version: number;
  mandate_version: number;
  paused: boolean;
}

export interface BeMandate {
  version: number;
  status: "DRAFT" | "ACTIVE" | "SUPERSEDED";
  cash_floor: Cents;
  act_alone_enabled: boolean;
  max_delay_days: number;
  max_amount_per_action: Cents;
  max_amount_per_day: Cents;
  protected_categories: BeCategory[];
  credit_enabled: boolean;
  max_credit_amount: Cents;
  paused: boolean;
}

export interface BeForecastSummary {
  first_breach: { date: string; closing: Cents; shortfall: Cents } | null;
  lowest: { date: string; closing: Cents };
  days_below_floor: number;
  end_balance: Cents;
  headroom_at_lowest: Cents;
}

export interface BeForecastDay {
  date: string;
  closing: Cents;
  credit_available: Cents;
  below_floor: boolean;
}

export interface BeForecastWeek {
  week: number;
  start_date: string;
  end_date: string;
  inflow: Cents;
  outflow: Cents;
  closing: Cents;
  lowest: Cents;
  any_below_floor: boolean;
}

export interface BeForecast {
  as_of_date: string;
  horizon_days: number;
  start_balance: Cents;
  cash_floor: Cents;
  summary: BeForecastSummary;
  days: BeForecastDay[];
  credit_lines: { amount: Cents; available_from: string; available_until: string }[];
  weeks: BeForecastWeek[] | null;
}

export interface BeExplanation {
  text: string;
  source: "MODEL" | "REPLAY" | "TEMPLATE";
}

export interface BeImpactState {
  first_breach: string | null;
  lowest: Cents;
  lowest_date: string;
  days_below_floor: number;
}

export interface BeRescheduleSubject {
  kind: "RESCHEDULE_PAYMENT";
  counterparty: string;
  category: BeCategory;
  amount: Cents;
  from_date: string;
  to_date: string;
}

export interface BeCreditLineSubject {
  kind: "CREDIT_LINE";
  amount: Cents;
  available_from: string;
  available_until: string;
  shortfall: Cents;
}

export type BeLane = "DONE_WITHIN_MANDATE" | "NEEDS_APPROVAL" | "NOT_ALLOWED";
export type BeRecommendationStatus =
  | "EXECUTED"
  | "PENDING"
  | "APPROVED_EXECUTED"
  | "REJECTED"
  | "BLOCKED"
  | "STALE"
  | "SUPERSEDED";

/** Vero Proof: keep paying `amount` (what the contract allows) instead of `billed_amount`. */
export interface BeKeepPaymentSubject {
  kind: "KEEP_PAYMENT";
  finding_id: string;
  cost_ref: string;
  counterparty: string;
  category: BeCategory | null;
  amount: Cents;
  billed_amount: Cents;
  effective_from: string | null;
  payments: { outflow_id: string; ref: string; date: string }[];
}

/** Vero Proof: pay `to_amount` instead of `from_amount` for these payments. */
export interface BeUpdateAmountSubject {
  kind: "UPDATE_RECURRING_AMOUNT";
  finding_id: string;
  cost_ref: string;
  counterparty: string;
  category: BeCategory | null;
  from_amount: Cents;
  to_amount: Cents;
  effective_from: string;
  payments: { outflow_id: string; ref: string; date: string }[];
}

/** Vero Proof: a letter asking the counterparty for the basis of a change. German only. */
export interface BeLetterSubject {
  kind: "SEND_LETTER";
  finding_id: string;
  counterparty: string;
  counterparty_id: string | null;
  letter_subject: string;
  language: "de";
  draft: string;
  draft_source: "MODEL" | "TEMPLATE";
}

export type BeCashSubject = BeRescheduleSubject | BeCreditLineSubject;
export type BeProofSubject = BeKeepPaymentSubject | BeUpdateAmountSubject | BeLetterSubject;

export interface BeRecommendation<S = BeCashSubject | BeProofSubject> {
  id: string;
  run_id: string;
  kind: "RESCHEDULE_PAYMENT" | "CREDIT_LINE" | "KEEP_PAYMENT" | "UPDATE_RECURRING_AMOUNT" | "SEND_LETTER";
  lane: BeLane;
  status: BeRecommendationStatus;
  subject: S;
  reason: { first_breach: string | null; shortfall: Cents; requested_by?: "OWNER" | null };
  checks: { rule: string; result: "PASS" | "FAIL" | "INFO"; effect: "NEEDS_APPROVAL" | "NOT_ALLOWED" | null; text: string }[];
  impact: { before: BeImpactState; after_if_executed: BeImpactState };
  explanation: BeExplanation;
  executed: { confirmation_ref: string; executed_by: "VERO" | "OWNER"; executed_at: string } | null;
  created_at: string;
  decided_at: string | null;
}

export interface BeOverview {
  company: BeCompany;
  cash_floor: Cents;
  lanes: {
    ANALYSED: { kind: "DRIVER" | "RECURRING" | "FX_EXPOSURE" | "BACKTEST"; text: string }[];
    DONE_WITHIN_MANDATE: BeRecommendation[];
    NEEDS_APPROVAL: BeRecommendation[];
    NOT_ALLOWED: BeRecommendation[];
  };
}

export interface BeDecisionResult {
  recommendation: BeRecommendation;
  follow_up_recommendation_ids: string[];
}

export type BeRunStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";

export interface BeTraceStep {
  seq: number;
  /** RECEIVE, EXTRACT, CHECK, INVESTIGATE, TOOL_CALL, TOOL_RESULT, GROUND, PROPAGATE, POLICY, SUMMARY, FALLBACK, THINK … */
  kind: string;
  source: string;
  label: string;
  tool: string | null;
  data: Record<string, unknown> | null;
  at: string;
}

export interface BeRun {
  run_id: string;
  /** CASH: Vero Cash's liquidity runs; PROOF: a document's check (the cost agent). */
  kind: "CASH" | "PROOF";
  /** PROOF: the uploaded document. */
  document_id: string | null;
  /** PROOF: the findings it created. */
  finding_ids: string[];
  status: BeRunStatus;
  mode: "AGENT" | "FALLBACK" | string;
  trigger: { type: string; label: string };
  trace: BeTraceStep[];
  result: { recommendation_ids: string[]; headline: BeExplanation | null } | null;
}

export interface BeRunAccepted {
  run_id: string;
  status: BeRunStatus;
}

// ───────────────────────── Activity log (GET /audit, DATA_CONTRACTS §2.2 AuditEntry) ─────────────────────────

export interface BeAuditEntry {
  seq: number;
  /** UTC with milliseconds, exactly as hashed: "2025-05-05T08:00:00.000Z". */
  timestamp: string;
  actor: "OWNER" | "AGENT" | "RULES" | "SYSTEM";
  agent: "CASH" | "PROOF" | null;
  action: string;
  run_id: string | null;
  recommendation_id: string | null;
  lane: BeLane | null;
  execution: "SIMULATED" | null;
  model: string | null;
  prev_hash: string;
  hash: string;
}

// ───────────────────────── Vero Proof: cost memory and documents ─────────────────────────

/** [x0, y0, x1, y1] in PDF points, origin top-left of the page. */
export type BeBox = [number, number, number, number];

export interface BeCostItem {
  cost_ref: string; // RENT, UTIL, XFER, PAY, SOC, WTAX or a supplier
  kind: "FIXED" | "SUPPLIER";
  counterparty: string;
  category: BeCategory;
  currency: "EUR" | "CHF";
  expected_amount: Cents | null;
  frequency: "MONTHLY" | null;
  due_day: number | null;
  eur_amount: Cents | null;
  fx_rate: string | null;
  prices: { description: string; unit: string; unit_price: string; document_id: string }[];
  sources: { kind: "STATEMENT" | "CONTRACT" | "PRICE_LIST" | "INVOICE"; date: string; document_id: string | null }[];
}

export type BeDocumentType = "LEASE" | "CONTRACT" | "INVOICE" | "RENT_ADJUSTMENT" | "NOTICE" | "PRICE_LIST" | "PAYMENT_PLAN";

export interface BeDocumentSummary {
  id: string;
  type: BeDocumentType;
  title: string;
  title_en: string;
  counterparty: { id: string; name: string };
  date: string;
  language: "de" | "en" | "it";
  currency: "EUR" | "CHF";
  document_number: string | null;
  cost_ref: string | null;
  source: "DOCUMENTS" | "MAILBOX" | "UPLOAD";
  page_count: number;
  file_url: string;
}

export interface BeDocument extends BeDocumentSummary {
  pages: { page: number; width: number; height: number; blocks: { id: string; text: string; text_en: string; box: BeBox | null }[] }[];
  lines: { id: string; description: string; quantity: string; unit: string; unit_price: string; vat_rate: number; net: Cents }[];
  totals: { net: Cents; vat: Cents; gross: Cents } | null;
  clauses: { id: string; section: string; title: string; text: string; text_en: string; page: number; box: BeBox | null }[];
  rent_adjustment: { old_amount: Cents; new_amount: Cents; effective_from: string } | null;
  /** Every printed value, e.g. ref "lines.l1.unit_price", text "12,90 €", with its box. */
  highlights: { ref: string; page: number; text: string; box: BeBox }[];
}

// ───────────────────────── Proof findings (GET /findings, docs/contracts ProofFinding) ─────────────────────────

export type BeEvidenceKind =
  | "DOCUMENT"
  | "INVOICE_LINE"
  | "PRICE_LIST_ITEM"
  | "CLAUSE"
  | "TRANSACTION"
  | "EMAIL"
  | "COST_ITEM"
  | "REFERENCE_VALUE";

/** Only refs with a document_id can be opened (GET /documents/{id}); EMAIL and REFERENCE_VALUE have only `ref`. */
export interface BeEvidenceRef {
  kind: BeEvidenceKind;
  document_id?: string | null;
  item_id?: string | null;
  transaction_id?: string | null;
  cost_ref?: string | null;
  /** The id a tool returned: an email, a reference series, or `document#clause`. */
  ref?: string | null;
}

export type BeClassification = "VERIFIED" | "UNEXPLAINED" | "CONFLICT";
export type BeFindingStatus = "INGESTED" | "EXTRACTED" | "CHECKED" | "INVESTIGATING" | "CLASSIFIED" | "PENDING_APPROVAL" | "RESOLVED";

export interface BeFinding {
  id: string;
  title: string;
  kind: "INVOICE_PRICES" | "RECURRING_AMOUNT";
  classification: BeClassification;
  /** UNEXPLAINED after an investigation that could not find a reason. */
  escalated: boolean;
  status: BeFindingStatus;
  counterparty: string;
  counterparty_id: string | null;
  cost_ref: string;
  period_from: string | null;
  period_to?: string | null;
  /** The document that billed it. */
  document_id: string | null;
  previous_document_id?: string | null;
  differences: {
    kind: "PRICE_CHANGE" | "NEW_ITEM" | "NEW_CHARGE" | "AMOUNT_CHANGE";
    description: string;
    delta: Cents;
    bps?: number | null;
    explained: boolean;
    evidence: BeEvidenceRef[];
  }[];
  impact: { currency: string; expected: Cents | null; billed: Cents | null; delta: Cents; bps: number | null } | null;
  evidence_refs: BeEvidenceRef[];
  investigation: {
    mode: "AGENT" | "FALLBACK";
    formula_id: string | null;
    inputs: Record<string, unknown> | null;
    outputs: Record<string, unknown> | null;
    billed: Cents | null;
    justified: Cents | null;
    disputed: Cents | null;
    searched: string[];
    problems: string[];
    note: string | null;
  } | null;
  propagation: {
    decision: "KEEP" | "UPDATE" | "PAID";
    cost_ref: string;
    effective_from: string | null;
    current_amount: Cents | null;
    billed_amount: Cents | null;
    proposed_amount: Cents | null;
    disputed: Cents | null;
    disputed_annual: Cents | null;
    disputed_in_window: Cents | null;
    forecast: { payments: string[]; outflow_difference: Cents; lowest_before: Cents; lowest_after: Cents } | null;
  } | null;
  recommendation_ids: string[];
  /** Uploads only; preloaded findings have no run. */
  run_id: string | null;
  acknowledged_at: string | null;
  created_at: string;
  updated_at: string;
  history: { at: string; status: BeFindingStatus; classification: BeClassification | null; label: string }[];
  /** Handled without the owner: verified, nothing to decide. */
  quiet: boolean;
  /** Shown until acknowledged (quiet) or its actions are decided (RESOLVED). */
  in_digest: boolean;
}

// ───────────────────────── Digest (GET /digest) and upload (POST /documents) ─────────────────────────

export interface BeDigest {
  as_of: string;
  counts: { changes: number; handled_quietly: number; needs_owner: number; routine_checks: number };
  changes: {
    finding_id: string;
    title: string;
    counterparty: string;
    cost_ref: string;
    classification: BeClassification;
    escalated: boolean;
    status: BeFindingStatus;
    quiet: boolean;
    investigation_mode: "AGENT" | "FALLBACK" | null;
    reason: string;
    actions: { recommendation_id: string; kind: BeRecommendation["kind"]; lane: BeLane; text: string }[];
  }[];
  routine_checks: {
    cost_ref: string;
    counterparty: string;
    transaction_id: string;
    date: string;
    paid: Cents;
    expected: Cents;
    currency: string;
    eur_amount: Cents | null;
    fx_rate: string | null;
    document_id: string;
  }[];
}

/** 202 from POST /documents. Poll GET /runs/{run_id} until SUCCEEDED or FAILED. */
export interface BeUploadAccepted {
  document_id: string;
  run_id: string;
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
}
