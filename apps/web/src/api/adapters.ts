/**
 * Real backend (backend.ts) → UI types (types.ts). The screens only ever see the UI types,
 * so mock mode and live mode render through the same components.
 */
import { clauseName, indexRentVerdict, paymentPropagation, searchedSteps, traceSteps } from "../features/vero/story";
import { formatDate, formatEur, formatEurWhole } from "../lib/format";
import type {
  BeAuditEntry,
  BeClassification,
  BeDigest,
  BeEvidenceRef,
  BeRun,
  BeCategory,
  BeCompany,
  BeCostItem,
  BeDocument,
  BeDocumentSummary,
  BeDocumentType,
  BeEvidenceKind,
  BeFinding,
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
  Classification,
  CostCheck,
  CostMemoryRow,
  Digest,
  DocLine,
  DocumentRun,
  EvidenceRef,
  FindingStatus,
  RecommendedAction,
  KnownDocument,
  SourceDoc,
  Decision,
  DecisionStatus,
  Fact,
  Finding,
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
    daysBelowFloor: f.summary.days_below_floor,
    firstBreachDate: f.summary.first_breach?.date,
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
  let text: string;
  switch (s.kind) {
    case "RESCHEDULE_PAYMENT":
      text = `${done ? "Moved" : "Move"} ${s.counterparty} (${formatEurWhole(eur(s.amount))}) from ${formatDate(s.from_date)} to ${formatDate(s.to_date)}`;
      break;
    case "CREDIT_LINE":
      text = `${done ? "Arranged" : "Arrange"} a ${formatEurWhole(eur(s.amount))} short-term credit line from ${formatDate(s.available_from)}`;
      break;
    case "KEEP_PAYMENT":
      text = `Keep paying ${s.counterparty} ${formatEur(eur(s.amount))}, not the ${formatEur(eur(s.billed_amount))} billed`;
      break;
    case "UPDATE_RECURRING_AMOUNT":
      text = `Pay ${s.counterparty} ${formatEur(eur(s.to_amount))} instead of ${formatEur(eur(s.from_amount))} from ${formatDate(s.effective_from)}`;
      break;
    case "SEND_LETTER":
      text = `${done ? "Sent" : "Send"} a letter to ${s.counterparty} asking for the basis of the change`;
      break;
  }
  return r.lane === "NOT_ALLOWED" ? `Not allowed by your rules: ${text[0].toLowerCase()}${text.slice(1)}` : text;
}

/** The euro amount a recommendation is about (letters have none). */
function amountOf(s: BeRecommendation["subject"]): number | undefined {
  switch (s.kind) {
    case "UPDATE_RECURRING_AMOUNT":
      return eur(s.to_amount);
    case "SEND_LETTER":
      return undefined;
    default:
      return eur(s.amount);
  }
}

const labels: Record<BeRecommendation["kind"], { approve: string; approved: string }> = {
  RESCHEDULE_PAYMENT: { approve: "Move payment", approved: "Payment moved (simulated)." },
  CREDIT_LINE: { approve: "Request credit line", approved: "Credit line requested (simulated)." },
  KEEP_PAYMENT: { approve: "Keep the amount", approved: "Payment amount kept (simulated)." },
  UPDATE_RECURRING_AMOUNT: { approve: "Change the amount", approved: "Payment amount changed (simulated)." },
  SEND_LETTER: { approve: "Send the letter", approved: "Letter sent (simulated). Nothing left the app." },
};

const arrow = (a: string, b: string) => (a === b ? a : `${a} → ${b}`);

function factsOf(r: BeRecommendation): Fact[] {
  const s = r.subject;
  if (s.kind === "KEEP_PAYMENT" || s.kind === "SEND_LETTER") {
    const facts: Fact[] = [];
    if (s.kind === "KEEP_PAYMENT") {
      facts.push({ label: "Billed", value: formatEur(eur(s.billed_amount)), tone: "bad" });
      facts.push({ label: "Contract allows", value: formatEur(eur(s.amount)), tone: "good" });
    }
    if (r.reason.disputed) facts.push({ label: "Disputed a month", value: formatEur(eur(r.reason.disputed)), tone: "bad" });
    return facts;
  }
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

const PROOF_KINDS = new Set<BeRecommendation["kind"]>(["KEEP_PAYMENT", "UPDATE_RECURRING_AMOUNT", "SEND_LETTER"]);

export function toDecision(r: BeRecommendation): Decision {
  const label = labels[r.kind] ?? labels.RESCHEDULE_PAYMENT;
  return {
    id: r.id,
    agent: PROOF_KINDS.has(r.kind) ? "costs" : "liquidity",
    autonomy: autonomy[r.lane],
    execution: "simulated",
    status: statusOf(r),
    createdAt: r.created_at,
    headline: headlineOf(r),
    summary: r.explanation.text,
    amountEur: amountOf(r.subject),
    facts: factsOf(r),
    reasoning: reasoningOf(r),
    approveLabel: label.approve,
    rejectLabel: "Not now",
    approvedMessage: label.approved,
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
      line: { id: c.id, text: `${c.section} ${c.title}: ${c.text}`, en: gloss(c.text, c.text_en) },
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
  // The analysis is made when the demo data is loaded: 08:00 UTC on the demo date (the backend's
  // demo_load_time, also the first activity log entry), not at midnight.
  const at = `${o.company.as_of_date}T08:00:00Z`;
  const analysed: Decision[] = o.lanes.ANALYSED.map((f, i) => ({
    id: `analysed-${i}-${f.kind}`,
    agent: "liquidity",
    autonomy: "analysis",
    insightKind: f.kind,
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

// ───────────────────────── Cost checks for the Overview ─────────────────────────

const evidenceWord: Record<BeEvidenceKind, string> = {
  DOCUMENT: "documents",
  INVOICE_LINE: "the invoice",
  PRICE_LIST_ITEM: "your price list",
  CLAUSE: "your contract",
  TRANSACTION: "your bank statement",
  EMAIL: "your emails",
  COST_ITEM: "your usual costs",
  REFERENCE_VALUE: "official reference values",
};

/** A real finding (GET /findings), reduced to what the Overview says about it. */
export function toCostCheck(f: BeFinding): CostCheck {
  const outcome: CostCheck["outcome"] =
    f.classification === "VERIFIED" ? "matches" : f.classification === "CONFLICT" ? "conflict" : "needs_evidence";
  return {
    id: f.id,
    title: f.title,
    counterparty: f.counterparty,
    outcome,
    // In the digest and not quiet: waiting for a decision or for evidence (decided or acknowledged ones leave it).
    needsOwner: f.in_digest && !f.quiet,
    deltaEur: f.impact?.delta != null ? eur(f.impact.delta) : undefined,
    evidence: [...new Set(f.differences.flatMap((d) => d.evidence.map((e) => evidenceWord[e.kind])).filter(Boolean))],
  };
}

const mockEvidence: Record<EvidenceRef["type"], string> = {
  statement: "your bank statement",
  notice: "the notice",
  lease: "your lease",
  contract: "your contract",
  invoice: "the invoice",
  price_list: "your price list",
  email: "your emails",
  reference: "official reference values",
  cost_memory: "your usual costs",
};

/** The mock's finding, reduced the same way. */
export function mockToCostCheck(f: Finding): CostCheck {
  const outcome: CostCheck["outcome"] =
    f.classification === "verified" ? "matches" : f.classification === "conflict" ? "conflict" : "needs_evidence";
  return {
    id: f.finding_id,
    title: `${f.vendor}: ${f.title}`,
    counterparty: f.vendor,
    outcome,
    needsOwner: !f.quiet && !f.acknowledged && f.status !== "resolved",
    deltaEur: f.line_item.current_value - f.line_item.previous_value,
    evidence: [...new Set(f.evidence_refs.map((e) => mockEvidence[e.type]).filter(Boolean))],
  };
}

// ───────────────────────── Vero Proof: findings, digest, uploads ─────────────────────────

const classOf = (c: BeClassification, escalated = false): Classification =>
  c === "VERIFIED" ? "verified" : c === "CONFLICT" ? "conflict" : escalated ? "escalated" : "unexplained";

const docType = (id: string): EvidenceRef["type"] =>
  id.startsWith("lease_")
    ? "lease"
    : id.startsWith("inv_")
      ? "invoice"
      : id.startsWith("pl_")
        ? "price_list"
        : id.startsWith("ctr_") || id.startsWith("terms_")
          ? "contract"
          : "notice";

const docLabel: Record<EvidenceRef["type"], string> = {
  lease: "Lease",
  invoice: "Invoice",
  price_list: "Price list",
  contract: "Contract",
  notice: "Letter",
  statement: "Bank statement",
  email: "Email",
  reference: "Reference values (demo)",
  cost_memory: "Cost Memory",
};

/** Evidence refs → one entry per document (a cited clause wins over the whole document), then the rest. */
function evidenceOf(refs: BeEvidenceRef[]): EvidenceRef[] {
  const byDoc = new Map<string, EvidenceRef>();
  const other: EvidenceRef[] = [];
  for (const r of refs) {
    if (r.document_id) {
      const type = docType(r.document_id);
      const clause = r.kind === "CLAUSE" ? clauseName(r.item_id) : "";
      const prev = byDoc.get(r.document_id);
      if (!prev || (clause && !prev.item_id)) {
        byDoc.set(r.document_id, {
          type,
          doc_id: r.document_id,
          item_id: r.kind === "CLAUSE" ? (r.item_id ?? undefined) : undefined,
          label: clause ? `${docLabel[type]} ${clause}` : docLabel[type],
          ref: r.ref ?? undefined,
        });
      }
    } else if (r.kind === "EMAIL" || r.kind === "REFERENCE_VALUE") {
      const type = r.kind === "EMAIL" ? "email" : "reference";
      other.push({ type, doc_id: null, label: docLabel[type], ref: r.ref ?? undefined });
    } else if (r.kind === "COST_ITEM") {
      other.push({ type: "cost_memory", doc_id: null, label: `Cost Memory: ${costLabel[r.cost_ref ?? ""] ?? r.cost_ref}`, ref: r.cost_ref ?? undefined });
    } else if (r.kind === "TRANSACTION") {
      other.push({ type: "statement", doc_id: null, label: "Bank statement", ref: r.transaction_id ?? undefined });
    }
  }
  return [...byDoc.values(), ...other];
}

const actionStatus = (s: BeRecommendation["status"]): RecommendedAction["status"] =>
  s === "PENDING" ? "pending" : s === "APPROVED_EXECUTED" || s === "EXECUTED" ? "approved" : s === "REJECTED" ? "rejected" : "other";

/** A Proof recommendation as one of a finding's prepared actions. */
function toAction(r: BeRecommendation): RecommendedAction | null {
  const s = r.subject;
  const base = { id: r.id, status: actionStatus(r.status), execution: "simulated" as const, detail: r.explanation.text };
  switch (s.kind) {
    case "KEEP_PAYMENT":
      return {
        ...base,
        kind: "keep_payment",
        title: `Keep paying ${formatEur(eur(s.amount))}${s.effective_from ? ` from ${formatDate(s.effective_from)}` : ""}, not ${formatEur(eur(s.billed_amount))}`,
      };
    case "UPDATE_RECURRING_AMOUNT":
      return {
        ...base,
        kind: "update_amount",
        title: `Pay ${formatEur(eur(s.to_amount))} instead of ${formatEur(eur(s.from_amount))} from ${formatDate(s.effective_from)}`,
      };
    case "SEND_LETTER":
      return {
        ...base,
        kind: "send_letter",
        title: `Send a letter to ${s.counterparty}, in German`,
        draft: { kind: "counter_proposal", to: s.counterparty, subject: s.letter_subject, body: s.draft, language: "de", source: s.draft_source },
      };
    default:
      return null;
  }
}

const findingStatus = (s: BeFinding["status"]) => s.toLowerCase() as FindingStatus;
const num = (v: unknown) => Number(v);

/** The verdict: index rent (lease §4.2) in its own words, any other formula as billed / allowed / disputed. */
function verdictOf(f: BeFinding, expected: number, billed: number, effective: string): Finding["verdict"] {
  const inv = f.investigation;
  const prop = f.propagation;
  if (!inv || inv.justified === null) return undefined;
  const justified = eur(inv.justified);
  const disputed = inv.disputed !== null ? eur(inv.disputed) : 0;
  const bill = inv.billed !== null ? eur(inv.billed) : billed;
  if (inv.formula_id === "vpi_threshold_indexation" && inv.inputs && inv.outputs) {
    return indexRentVerdict({
      clause: clauseName(f.evidence_refs.find((r) => r.kind === "CLAUSE")?.item_id) || "the index clause",
      baseIndex: num(inv.inputs.base_index) / 10,
      currentIndex: num(inv.inputs.current_index) / 10,
      thresholdBps: num(inv.inputs.threshold_bps),
      changeBps: num(inv.outputs.change_bps),
      allowed: inv.outputs.adjustment_allowed === true,
      current: inv.inputs.base_amount !== undefined ? eur(String(inv.inputs.base_amount)) : expected,
      billed: bill,
      justified,
      disputed,
      disputedAnnual: prop?.disputed_annual ? eur(prop.disputed_annual) : disputed * 12,
      disputedInWindow: prop?.disputed_in_window ? eur(prop.disputed_in_window) : undefined,
      payments: prop?.forecast?.payments ?? [],
      effectiveFrom: effective,
      sourceDoc: f.document_id,
    });
  }
  return {
    title:
      disputed > 0
        ? `${f.counterparty} bills ${formatEur(bill)}. The evidence allows ${formatEur(justified)}.`
        : `The evidence allows the ${formatEur(bill)} billed.`,
    detail: f.history.at(-1)?.label ?? "",
    cells: [
      { k: "Billed", v: formatEur(bill), calc: f.document_id ?? "the bill" },
      { k: "Allowed", v: formatEur(justified), calc: inv.formula_id ?? "the evidence", tone: "good" },
      { k: "Disputed", v: formatEur(disputed), calc: `${formatEur(bill)} − ${formatEur(justified)}`, tone: disputed > 0 ? "bad" : undefined },
    ],
  };
}

/**
 * A real Proof finding for the cost check page. `recs` are its recommendations (GET /recommendations),
 * `run` the upload's run (GET /runs/{run_id}) for "How Vero found out"; the preloaded findings have
 * no run, so their investigation's `searched` list stands in.
 */
export function toFinding(f: BeFinding, recs: Map<string, BeRecommendation>, run?: BeRun | null): Finding {
  const inv = f.investigation;
  const prop = f.propagation;
  const recurring = f.kind === "RECURRING_AMOUNT";
  const label = costLabel[f.cost_ref] ?? f.counterparty;
  const expected = f.impact.expected !== null ? eur(f.impact.expected) : 0;
  const billed = f.impact.billed !== null ? eur(f.impact.billed) : expected + eur(f.impact.delta);
  const effective = prop?.effective_from ?? f.period_from ?? f.created_at.slice(0, 10);
  const labelled = f.history.filter((h) => h.classification !== null);
  const history = labelled.map((h) => classOf(h.classification!));
  if (history.length) history[history.length - 1] = classOf(f.classification, f.escalated);

  const actions = f.recommendation_ids.flatMap((id) => {
    const r = recs.get(id);
    const a = r ? toAction(r) : null;
    return a ? [a] : [];
  });
  const needsEvidence = f.classification === "UNEXPLAINED" && !f.quiet && (f.status === "CLASSIFIED" || f.status === "PENDING_APPROVAL");

  return {
    finding_id: f.id,
    status: findingStatus(f.status),
    classification: classOf(f.classification, f.escalated),
    classification_history: history,
    vendor: f.counterparty,
    title: recurring && f.differences.length === 1 ? f.differences[0].description : f.title,
    source_doc_id: f.document_id,
    line_item: {
      description: recurring ? (f.differences[0]?.description.replace(/ from .*$/, "") ?? label) : "Invoice total",
      previous_value: expected,
      current_value: billed,
      currency: "EUR",
      unit: recurring ? "month" : "invoice",
      effective_from: effective,
      monthly_variance: eur(f.impact.delta),
      variance_pct: (f.impact.bps ?? 0) / 100,
    },
    investigation_trace: run?.trace?.length ? traceSteps(run.trace) : searchedSteps(inv?.searched ?? []),
    reason: (labelled.at(-1) ?? f.history.at(-1))?.label ?? "",
    verdict: verdictOf(f, expected, billed, effective),
    financial_impact:
      inv && inv.justified !== null
        ? {
            justified_value: eur(inv.justified),
            disputed_amount_monthly: inv.disputed !== null ? eur(inv.disputed) : 0,
            disputed_amount_annual: prop?.disputed_annual ? eur(prop.disputed_annual) : 0,
            disputed_in_window: prop?.disputed_in_window ? eur(prop.disputed_in_window) : undefined,
          }
        : undefined,
    evidence_refs: evidenceOf(f.evidence_refs),
    propagation: prop
      ? paymentPropagation({
          decision: prop.decision,
          label,
          current: prop.current_amount !== null ? eur(prop.current_amount) : expected,
          proposed: prop.proposed_amount !== null ? eur(prop.proposed_amount) : expected,
          billed: prop.billed_amount !== null ? eur(prop.billed_amount) : billed,
          effectiveFrom: prop.effective_from,
          payments: prop.forecast?.payments ?? [],
          outflowDifference: prop.forecast ? eur(prop.forecast.outflow_difference) : 0,
          lowestBefore: prop.forecast ? eur(prop.forecast.lowest_before) : undefined,
          lowestAfter: prop.forecast ? eur(prop.forecast.lowest_after) : undefined,
        })
      : [],
    recommended_actions: actions,
    quiet: f.quiet,
    acknowledged: f.acknowledged_at !== null,
    evidence_request: needsEvidence
      ? {
          message: f.escalated
            ? `Vero looked for the reason (${inv?.searched.length ?? 0} look-ups) and found none. Nothing is changed until you send the evidence.`
            : "Vero hasn't found the reason for this change yet. Nothing is changed until then.",
          action_label: "Upload a bill or notice",
          href: "/contract",
        }
      : undefined,
    created_at: f.created_at,
    resolved_at: f.status === "RESOLVED" ? f.updated_at : undefined,
  };
}

const unitPrice = (v: string | null) => (v === null ? "" : formatEur(Number(v)));

/** One difference of a quiet change, as a line under it. */
function differenceLine(d: BeFinding["differences"][number]): string {
  switch (d.kind) {
    case "PRICE_CHANGE":
      return `${d.description}: ${unitPrice(d.previous_unit_price)} → ${unitPrice(d.unit_price)}`;
    case "NEW_ITEM":
      return `${d.description}: new, ${unitPrice(d.unit_price)}`;
    case "NEW_CHARGE":
      return `${d.description}: ${formatEur(eur(d.delta))}, new`;
    default:
      return `${d.description}: ${d.expected_amount ? formatEur(eur(d.expected_amount)) : "?"} → ${d.billed_amount ? formatEur(eur(d.billed_amount)) : "?"}`;
  }
}

const chfFormat = new Intl.NumberFormat("en-GB", { style: "currency", currency: "CHF" });

/** GET /digest, with the differences behind each quiet change from GET /findings. */
export function toDigest(d: BeDigest, findings: BeFinding[]): Digest {
  const byId = new Map(findings.map((f) => [f.id, f]));
  const money = (cents: Cents, currency: string, eurCents: Cents | null) =>
    currency === "CHF"
      ? `${chfFormat.format(eur(cents))}${eurCents ? ` (${formatEur(eur(eurCents))})` : ""}`
      : formatEur(eur(cents));
  return {
    counts: d.counts,
    quiet: d.changes
      .filter((c) => c.quiet)
      .map((c) => {
        const f = byId.get(c.finding_id);
        return {
          id: c.finding_id,
          classification: classOf(c.classification, c.escalated),
          title: c.title,
          detail: c.reason,
          doc_id: f?.document_id ?? null,
          calc: f?.differences.map(differenceLine),
        };
      }),
    routine: d.routine_checks.map((r) => ({
      cost_ref: r.cost_ref,
      counterparty: r.counterparty,
      date: r.date,
      paid: money(r.paid, r.currency, r.eur_amount),
      expected: money(r.expected, r.currency, null),
      matches: r.matches,
      doc_id: r.document_id,
    })),
  };
}

/** An upload's run (GET /runs/{id}), for the upload page's progress. */
export function toDocumentRun(r: BeRun): DocumentRun {
  const failure = r.status === "FAILED" ? r.trace.filter((t) => t.kind === "FALLBACK").at(-1) : undefined;
  const colon = failure?.label.indexOf(": ") ?? -1;
  return {
    checked: r.trace.some((t) => t.kind === "CHECK"),
    reason: failure?.data?.reason ?? undefined,
    detail: failure && colon > 0 ? failure.label.slice(colon + 2) : undefined,
    document_id: r.document_id ?? "",
    run_id: r.run_id,
    status: r.status === "SUCCEEDED" || r.status === "FAILED" ? r.status : "RUNNING",
    steps: r.trace.map((t) => t.label),
    headline: r.result?.headline?.text,
    finding_ids: r.finding_ids ?? [],
  };
}
