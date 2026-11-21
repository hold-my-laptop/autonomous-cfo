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

export interface BeRecommendation {
  id: string;
  run_id: string;
  kind: "RESCHEDULE_PAYMENT" | "CREDIT_LINE";
  lane: BeLane;
  status: BeRecommendationStatus;
  subject: BeRescheduleSubject | BeCreditLineSubject;
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

export interface BeRun {
  run_id: string;
  status: BeRunStatus;
  trigger: { type: string; label: string };
  trace: { seq: number; label: string }[];
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

// ───────────────────────── Proof findings (GET /findings), the parts the Overview reads ─────────────────────────

export type BeEvidenceKind =
  | "DOCUMENT"
  | "INVOICE_LINE"
  | "PRICE_LIST_ITEM"
  | "CLAUSE"
  | "TRANSACTION"
  | "EMAIL"
  | "COST_ITEM"
  | "REFERENCE_VALUE";

export interface BeFinding {
  id: string;
  title: string;
  kind: "INVOICE_PRICES" | "RECURRING_AMOUNT";
  classification: "VERIFIED" | "UNEXPLAINED" | "CONFLICT";
  escalated: boolean;
  status: "INGESTED" | "EXTRACTED" | "CHECKED" | "INVESTIGATING" | "CLASSIFIED" | "PENDING_APPROVAL" | "RESOLVED";
  counterparty: string;
  impact: { currency: string; expected: Cents | null; billed: Cents | null; delta: Cents | null; bps: number | null } | null;
  differences: { kind: string; description: string; explained: boolean; evidence: { kind: BeEvidenceKind }[] }[];
  recommendation_ids: string[];
  acknowledged_at: string | null;
}
