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
