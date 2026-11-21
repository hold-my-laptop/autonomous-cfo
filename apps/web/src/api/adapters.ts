/**
 * Real backend (backend.ts) → UI types (types.ts). The screens only ever see the UI types,
 * so mock mode and live mode render through the same components.
 */
import { formatDate, formatEurWhole } from "../lib/format";
import type {
  BeCategory,
  BeCompany,
  BeForecast,
  BeImpactState,
  BeMandate,
  BeOverview,
  BeRecommendation,
  Cents,
} from "./backend";
import type {
  AutonomyLevel,
  Company,
  Decision,
  DecisionStatus,
  Fact,
  Forecast,
  ForecastWeek,
  PaymentCategory,
  Policy,
  ReasoningStep,
  RiskLevel,
} from "./types";

/** "123456" (cents) → 1234.56 (euros) */
export const eur = (cents: Cents): number => Number(cents) / 100;

const category: Record<BeCategory, PaymentCategory> = {
  PAYROLL: "salary",
  SOCIAL_SECURITY: "social_security",
  TAX: "tax",
  RENT: "rent",
  UTILITIES: "utilities",
  PARTNER_TRANSFER: "fx_transfer",
  VARIABLE_COST: "supplier",
  CARD_REVENUE: "other",
  OTHER: "other",
};

// ───────────────────────── Company & policy ─────────────────────────

export function toCompany(c: BeCompany, mandate: BeMandate): Company {
  return {
    id: "company",
    name: c.name,
    ownerFirstName: c.owner.split(/\s+/)[0] ?? c.owner,
    currency: "EUR",
    asOf: c.as_of_date,
    cashTodayEur: eur(c.balance),
    safetyBufferEur: eur(mandate.cash_floor),
  };
}

export function toPolicy(m: BeMandate): Policy {
  return {
    autoApproveMaxEur: eur(m.max_amount_per_action),
    canMoveFlexiblePayments: m.act_alone_enabled && !m.paused,
    protectedCategories: [...new Set(m.protected_categories.map((c) => category[c]))],
    executionMode: "simulated",
  };
}

// ───────────────────────── Forecast ─────────────────────────

export function toForecast(f: BeForecast, fetchedAt: string): Forecast {
  const floor = eur(f.cash_floor);
  const beWeeks = f.weeks ?? [];

  const weeks: ForecastWeek[] = beWeeks.map((w) => {
    const days = f.days.filter((d) => d.date >= w.start_date && d.date <= w.end_date);
    // Red: the balance goes past the arranged credit (a real overdraft). Yellow: below the floor.
    const overdrawn = days.some((d) => eur(d.closing) + eur(d.credit_available) < 0);
    const status: RiskLevel = overdrawn ? "red" : w.any_below_floor ? "yellow" : "green";
    return {
      week: w.week,
      startDate: w.start_date,
      inflowEur: eur(w.inflow),
      outflowEur: eur(w.outflow),
      balanceEur: eur(w.closing),
      lowestEur: eur(w.lowest),
      bufferEur: floor,
      status,
    };
  });

  const lowestDate = f.summary.lowest.date;
  const lowestWeek = beWeeks.find((w) => lowestDate >= w.start_date && lowestDate <= w.end_date)?.week ?? 1;
  const overdraft = f.credit_lines.reduce((sum, c) => sum + eur(c.amount), 0);

  return {
    generatedAt: fetchedAt,
    madeOn: f.as_of_date,
    horizonDays: f.horizon_days,
    startingBalanceEur: eur(f.start_balance),
    weeks,
    // The backend has no likely range yet: only the balance line is drawn.
    days: f.days.map((d) => ({ date: d.date, balanceEur: eur(d.closing) })),
    firstShortDate: beWeeks.find((w) => eur(w.closing) < 0)?.start_date,
    lowestBalanceEur: eur(f.summary.lowest.closing),
    lowestDate,
    lowestWeek,
    spareCashEur: Math.max(0, eur(f.summary.headroom_at_lowest)),
    overdraftLimitEur: overdraft > 0 ? overdraft : undefined,
  };
}

// ───────────────────────── Recommendations → decisions ─────────────────────────

const autonomy: Record<BeRecommendation["lane"], AutonomyLevel> = {
  DONE_WITHIN_MANDATE: "done",
  NEEDS_APPROVAL: "approval_required",
  NOT_ALLOWED: "analysis",
};

function statusOf(r: BeRecommendation): DecisionStatus {
  switch (r.status) {
    case "PENDING":
      return "pending";
    case "APPROVED_EXECUTED":
      return "approved";
    case "REJECTED":
      return "rejected";
    case "EXECUTED":
      return "done";
    default:
      return "info"; // BLOCKED, STALE, SUPERSEDED
  }
}

function headlineOf(r: BeRecommendation): string {
  const done = r.lane === "DONE_WITHIN_MANDATE";
  const s = r.subject;
  const text =
    s.kind === "RESCHEDULE_PAYMENT"
      ? `${done ? "Moved" : "Move"} ${s.counterparty} (${formatEurWhole(eur(s.amount))}) from ${formatDate(s.from_date)} to ${formatDate(s.to_date)}`
      : `${done ? "Arranged" : "Arrange"} a ${formatEurWhole(eur(s.amount))} short-term credit line from ${formatDate(s.available_from)}`;
  return r.lane === "NOT_ALLOWED" ? `Not allowed by your rules: ${text[0].toLowerCase()}${text.slice(1)}` : text;
}

const arrow = (a: string, b: string) => (a === b ? a : `${a} → ${b}`);

function factsOf(r: BeRecommendation): Fact[] {
  const { before, after_if_executed: after } = r.impact;
  const facts: Fact[] = [
    {
      label: "Lowest balance",
      value: arrow(formatEurWhole(eur(before.lowest)), formatEurWhole(eur(after.lowest))),
      tone: eur(after.lowest) > eur(before.lowest) ? "good" : eur(after.lowest) < 0 ? "bad" : "neutral",
    },
    {
      label: "Days below floor",
      value: arrow(String(before.days_below_floor), String(after.days_below_floor)),
      tone:
        after.days_below_floor < before.days_below_floor
          ? "good"
          : after.days_below_floor > before.days_below_floor
            ? "bad"
            : "neutral",
    },
  ];
  if (eur(r.reason.shortfall) > 0) {
    facts.push({ label: "Shortfall", value: formatEurWhole(eur(r.reason.shortfall)), tone: "bad" });
  }
  return facts;
}

const impactText = (s: BeImpactState) =>
  `lowest ${formatEurWhole(eur(s.lowest))} on ${formatDate(s.lowest_date)}, ${s.days_below_floor} days below the floor`;

function reasoningOf(r: BeRecommendation): ReasoningStep[] {
  const steps: ReasoningStep[] = [];
  if (r.reason.requested_by === "OWNER") {
    steps.push({ kind: "analysis", title: "You asked for this change", detail: "The same rules apply as for Vero's own ideas." });
  } else if (r.reason.first_breach) {
    steps.push({
      kind: "analysis",
      title: `Cash falls below your floor on ${formatDate(r.reason.first_breach)}`,
      detail: `${formatEurWhole(eur(r.reason.shortfall))} short of your floor.`,
    });
  }
  for (const c of r.checks) {
    steps.push({
      kind: "rule",
      title: c.text,
      detail:
        c.effect === "NEEDS_APPROVAL"
          ? "So this needs your OK."
          : c.effect === "NOT_ALLOWED"
            ? "So Vero may not do this."
            : `Rule ${c.rule}`,
      passed: c.result === "PASS" ? true : c.result === "FAIL" ? false : undefined,
    });
  }
  steps.push({
    kind: "calculation",
    title: "If this goes ahead",
    detail: `Before: ${impactText(r.impact.before)}. After: ${impactText(r.impact.after_if_executed)}.`,
  });
  if (r.executed) {
    steps.push({
      kind: "recommendation",
      title: "Done in simulation",
      detail: `Confirmation ${r.executed.confirmation_ref}. Nothing was sent to the bank and no money moved.`,
    });
  }
  return steps;
}

export function toDecision(r: BeRecommendation): Decision {
  const credit = r.subject.kind === "CREDIT_LINE";
  return {
    id: r.id,
    agent: "liquidity",
    autonomy: autonomy[r.lane],
    execution: "simulated",
    status: statusOf(r),
    createdAt: r.created_at,
    headline: headlineOf(r),
    summary: r.explanation.text,
    amountEur: eur(r.subject.amount),
    facts: factsOf(r),
    reasoning: reasoningOf(r),
    approveLabel: credit ? "Request credit line" : "Move payment",
    rejectLabel: "Not now",
    approvedMessage: credit ? "Credit line requested (simulated)." : "Payment moved (simulated).",
    rejectedMessage: "OK. Nothing changes.",
    resolvedAt: r.decided_at ?? r.executed?.executed_at ?? undefined,
    resolvedBy: r.executed ? (r.executed.executed_by === "VERO" ? "agent" : "owner") : r.decided_at ? "owner" : undefined,
  };
}

const findingTitle = {
  DRIVER: "What drives your cash",
  RECURRING: "Recurring payment found",
  FX_EXPOSURE: "Swiss franc exposure",
  BACKTEST: "Forecast check",
} as const;

/** All lanes of GET /overview as one list; ANALYSED findings become analysis-only items. */
export function toDecisions(o: BeOverview): Decision[] {
  const at = `${o.company.as_of_date}T00:00:00Z`;
  const analysed: Decision[] = o.lanes.ANALYSED.map((f, i) => ({
    id: `analysed-${i}-${f.kind}`,
    agent: "liquidity",
    autonomy: "analysis",
    execution: "simulated",
    status: "info",
    createdAt: at,
    headline: f.text,
    summary: `${findingTitle[f.kind]}. Vero only looked at your data; nothing was changed.`,
    facts: [],
    reasoning: [],
  }));
  return [
    ...o.lanes.NEEDS_APPROVAL,
    ...o.lanes.DONE_WITHIN_MANDATE,
    ...o.lanes.NOT_ALLOWED,
  ]
    .map(toDecision)
    .concat(analysed);
}
