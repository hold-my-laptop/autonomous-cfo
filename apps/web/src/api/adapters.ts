/**
 * Real backend (backend.ts) → UI types (types.ts). The screens only ever see the UI types,
 * so mock mode and live mode render through the same components.
 */
import { formatDate, formatEurWhole } from "../lib/format";
import type {
  BeAuditEntry,
  BeCategory,
  BeCompany,
  BeCostItem,
  BeDocument,
  BeDocumentSummary,
  BeDocumentType,
  BeForecast,
  BeImpactState,
  BeMandate,
  BeOverview,
  BeRecommendation,
  Cents,
} from "./backend";
import type {
  AgentId,
  AuditActor,
  AuditEntry,
  AutonomyLevel,
  Company,
  CostMemoryRow,
  DocLine,
  KnownDocument,
  SourceDoc,
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

// ───────────────────────── Activity log ─────────────────────────

const auditActor: Record<BeAuditEntry["actor"], AuditActor> = { OWNER: "owner", AGENT: "agent", RULES: "rules", SYSTEM: "system" };
const auditAgent: Record<NonNullable<BeAuditEntry["agent"]>, AgentId> = { CASH: "liquidity", PROOF: "costs" };

/** For display only: verify the raw entry first (lib/hashChain.ts verifyBackendChain). */
export function toAuditEntry(e: BeAuditEntry): AuditEntry {
  return {
    seq: e.seq,
    timestamp: e.timestamp,
    actor: auditActor[e.actor],
    agent: e.agent ? auditAgent[e.agent] : undefined,
    autonomy: e.lane ? autonomy[e.lane] : undefined,
    execution: e.execution ? "simulated" : undefined,
    action: e.action,
    decisionId: e.recommendation_id ?? undefined,
    model: e.model ? { name: e.model } : undefined,
    prevHash: e.prev_hash,
    hash: e.hash,
  };
}

// ───────────────────────── Vero Proof: documents and cost memory ─────────────────────────

const documentKind: Record<BeDocumentType, KnownDocument["kind"]> = {
  LEASE: "lease",
  CONTRACT: "contract",
  PAYMENT_PLAN: "contract",
  PRICE_LIST: "contract",
  INVOICE: "invoice",
  RENT_ADJUSTMENT: "notice",
  NOTICE: "notice",
};
const documentSource: Record<BeDocumentSummary["source"], KnownDocument["source"]> = {
  DOCUMENTS: "history",
  MAILBOX: "inbox",
  UPLOAD: "upload",
};

export function toKnownDocument(d: BeDocumentSummary): KnownDocument {
  return {
    doc_id: d.id,
    kind: documentKind[d.type],
    label: d.title_en,
    note: [d.counterparty.name, d.document_number].filter(Boolean).join(" · "),
    source: documentSource[d.source],
    added_at: `${d.date}T00:00:00Z`,
  };
}

const viewerKind: Record<BeDocumentType, SourceDoc["kind"]> = {
  LEASE: "Lease",
  CONTRACT: "Contract",
  PAYMENT_PLAN: "Contract",
  PRICE_LIST: "Contract",
  INVOICE: "Invoice",
  RENT_ADJUSTMENT: "Notice",
  NOTICE: "Notice",
};

const deEur = (cents: Cents) => (Number(cents) / 100).toLocaleString("de-DE", { style: "currency", currency: "EUR" });

/**
 * The document as text lines for the viewer, in page and reading order (by each part's box):
 * text blocks and clauses (German, with the English gloss), invoice lines and totals, and a rent
 * adjustment. Values use the exact printed text from `highlights`. What is billed (invoice total,
 * new rent) is boxed as "billed", the old rent as the "basis". Boxes are PDF points with the origin
 * top-left; a rendered PDF would scale them by rendered width ÷ page.width.
 */
export function toSourceDoc(d: BeDocument): SourceDoc {
  const printed = (ref: string) => d.highlights.find((h) => h.ref === ref);
  const at = (ref: string, fallbackPage = 1): [number, number] => {
    const h = printed(ref);
    return h ? [h.page, h.box[1]] : [fallbackPage, Number.MAX_SAFE_INTEGER];
  };
  const gloss = (text: string, en: string) => (en && en !== text ? en : undefined);
  const parts: { page: number; y: number; line: DocLine }[] = [];

  for (const p of d.pages) {
    for (const b of p.blocks) {
      parts.push({ page: p.page, y: b.box?.[1] ?? 0, line: { text: b.text, en: gloss(b.text, b.text_en), muted: b.id === "letterhead" } });
    }
  }
  for (const c of d.clauses) {
    parts.push({
      page: c.page,
      y: c.box?.[1] ?? Number.MAX_SAFE_INTEGER,
      line: { text: `${c.section} ${c.title}: ${c.text}`, en: gloss(c.text, c.text_en) },
    });
  }
  for (const l of d.lines) {
    const [page, y] = at(`lines.${l.id}.description`);
    const v = (field: string, fallback: string) => printed(`lines.${l.id}.${field}`)?.text ?? fallback;
    parts.push({
      page,
      y,
      line: { text: `${l.description} · ${v("quantity", l.quantity)} ${v("unit", l.unit)} × ${v("unit_price", l.unit_price)} = ${v("net", deEur(l.net))}` },
    });
  }
  if (d.totals) {
    const t = d.totals;
    const [page, y] = at("totals.net");
    parts.push({ page, y, line: { text: `Summe netto ${printed("totals.net")?.text ?? deEur(t.net)} · MwSt. ${printed("totals.vat")?.text ?? deEur(t.vat)}`, en: "Net total · VAT" } });
    const [gp, gy] = at("totals.gross");
    parts.push({ page: gp, y: gy, line: { text: `Gesamtbetrag ${printed("totals.gross")?.text ?? deEur(t.gross)}`, en: "Total", strong: true, box: { label: "Billed", tone: "billed" } } });
  }
  if (d.rent_adjustment) {
    const r = d.rent_adjustment;
    const [op, oy] = at("rent_adjustment.old_amount");
    parts.push({ page: op, y: oy, line: { text: `Bisherige Miete ${printed("rent_adjustment.old_amount")?.text ?? deEur(r.old_amount)}`, en: "Rent so far", box: { label: "Basis", tone: "basis" } } });
    const [np, ny] = at("rent_adjustment.new_amount");
    parts.push({ page: np, y: ny, line: { text: `Neue Miete ab ${formatDate(r.effective_from)}: ${printed("rent_adjustment.new_amount")?.text ?? deEur(r.new_amount)}`, en: "New rent", strong: true, box: { label: "Billed", tone: "billed" } } });
  }

  const lines = parts.sort((a, b) => a.page - b.page || a.y - b.y).map((x) => x.line);
  return {
    doc_id: d.id,
    kind: viewerKind[d.type],
    title: d.title,
    meta: `${d.title_en} · ${d.counterparty.name} · ${formatDate(d.date)}${d.page_count > 1 ? ` · ${d.page_count} pages` : ""}`,
    page: 1,
    lines,
    file_url: d.file_url,
  };
}

const costLabel: Record<string, string> = {
  RENT: "Rent",
  UTIL: "Energy & utilities",
  XFER: "CHF transfer",
  PAY: "Payroll",
  SOC: "Social security",
  WTAX: "Wage tax",
};
const costSource: Record<BeCostItem["sources"][number]["kind"], CostMemoryRow["sources"][number]> = {
  STATEMENT: "statement",
  CONTRACT: "contract",
  PRICE_LIST: "price_list",
  INVOICE: "invoice",
};

export function toCostMemoryRow(c: BeCostItem): CostMemoryRow {
  return {
    cost_ref: c.cost_ref,
    label: costLabel[c.cost_ref] ?? (c.kind === "SUPPLIER" ? "Supplier" : c.cost_ref),
    vendor: c.counterparty,
    kind: c.kind === "FIXED" ? "fixed" : "supplier",
    expected:
      c.expected_amount !== null
        ? { amount: eur(c.expected_amount), currency: c.currency, eur_amount: c.eur_amount !== null ? eur(c.eur_amount) : undefined }
        : undefined,
    due_day: c.due_day ?? undefined,
    prices: c.prices.length,
    sources: [...new Set(c.sources.map((s) => costSource[s.kind]))],
  };
}

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
