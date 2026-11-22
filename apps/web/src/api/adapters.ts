/**
 * Real backend (backend.ts) → UI types (types.ts). The screens only ever see the UI types,
 * so mock mode and live mode render through the same components.
 */
import { formatDate, formatEur, formatEurWhole } from "../lib/format";
import type {
  BeAuditEntry,
  BeCashSubject,
  BeCategory,
  BeClassification,
  BeCompany,
  BeCostItem,
  BeDocument,
  BeDocumentSummary,
  BeDigest,
  BeDocumentType,
  BeEvidenceKind,
  BeEvidenceRef,
  BeFinding,
  BeFindingStatus,
  BeForecast,
  BeImpactState,
  BeLane,
  BeMandate,
  BeOverview,
  BeProofSubject,
  BeRecommendation,
  BeRun,
  Cents,
} from "./backend";
import type {
  AgentId,
  AuditActor,
  AuditEntry,
  AutonomyLevel,
  Classification,
  Company,
  CostCheck,
  CostMemoryRow,
  Digest,
  DocLine,
  EvidenceRef,
  FindingStatus,
  Formula,
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
  RecommendedAction,
  RiskLevel,
  ToolName,
  TraceStep,
  UploadRun,
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

type CashRecommendation = BeRecommendation<BeCashSubject>;

function statusOf(r: CashRecommendation): DecisionStatus {
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

function headlineOf(r: CashRecommendation): string {
  const done = r.lane === "DONE_WITHIN_MANDATE";
  const s = r.subject;
  const text =
    s.kind === "RESCHEDULE_PAYMENT"
      ? `${done ? "Moved" : "Move"} ${s.counterparty} (${formatEurWhole(eur(s.amount))}) from ${formatDate(s.from_date)} to ${formatDate(s.to_date)}`
      : `${done ? "Arranged" : "Arrange"} a ${formatEurWhole(eur(s.amount))} short-term credit line from ${formatDate(s.available_from)}`;
  return r.lane === "NOT_ALLOWED" ? `Not allowed by your rules: ${text[0].toLowerCase()}${text.slice(1)}` : text;
}

const arrow = (a: string, b: string) => (a === b ? a : `${a} → ${b}`);

function factsOf(r: CashRecommendation): Fact[] {
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

function reasoningOf(r: CashRecommendation): ReasoningStep[] {
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

export function toDecision(r: CashRecommendation): Decision {
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

/**
 * All lanes of GET /overview as one list; ANALYSED findings become analysis-only items.
 * The lanes also hold Vero Proof's actions (keep a payment, send a letter): those are decided on
 * the cost check page, with their finding, so only Vero Cash's recommendations become decisions here.
 */
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
    .filter(isCash)
    .map(toDecision)
    .concat(analysed);
}

// ───────────────────────── Vero Proof: findings, actions, trace, digest ─────────────────────────

const classification = (c: BeClassification, escalated: boolean): Classification =>
  c === "VERIFIED" ? "verified" : c === "CONFLICT" ? "conflict" : escalated ? "escalated" : "unexplained";

const findingStatus = (s: BeFindingStatus) => s.toLowerCase() as FindingStatus;

const pct = (bps: number) => Math.round(bps) / 100;
/** Index in tenths of a point (1212) → points (121.2). */
const indexPoints = (tenths: unknown) => Number(tenths) / 10;

function toFormula(id: string | null, inputs: Record<string, unknown> | null, outputs: Record<string, unknown> | null): Formula | undefined {
  if (!id || !inputs || !outputs) return undefined;
  if (id === "vpi_threshold_indexation") {
    return {
      formula_id: id,
      base_amount: eur(String(inputs.base_amount)),
      base_index: indexPoints(inputs.base_index),
      current_index: indexPoints(inputs.current_index),
      threshold_pct: pct(Number(inputs.threshold_bps)),
      change_pct: pct(Number(outputs.change_bps)),
      adjustment_allowed: Boolean(outputs.adjustment_allowed),
      justified: eur(String(outputs.justified)),
    };
  }
  if (id === "surcharge_rule") {
    return {
      formula_id: id,
      net_goods: eur(String(inputs.net_goods)),
      below_net_goods: eur(String(inputs.below_net_goods)),
      surcharge: eur(String(inputs.surcharge)),
      applies: Boolean(outputs.applies),
    };
  }
  return { formula_id: "other", name: id, inputs, outputs };
}

const evidenceKind: Record<BeEvidenceKind, EvidenceRef["kind"]> = {
  DOCUMENT: "document",
  CLAUSE: "clause",
  INVOICE_LINE: "invoice_line",
  PRICE_LIST_ITEM: "price_list_item",
  TRANSACTION: "transaction",
  EMAIL: "email",
  COST_ITEM: "cost_item",
  REFERENCE_VALUE: "reference",
};

const documentWord: Record<BeDocumentType, string> = {
  LEASE: "Lease",
  CONTRACT: "Terms",
  PAYMENT_PLAN: "Payment plan",
  PRICE_LIST: "Price list",
  INVOICE: "Invoice",
  RENT_ADJUSTMENT: "Rent adjustment",
  NOTICE: "Landlord's letter",
};

const referenceName: Record<string, string> = {
  ref_vpi_demo: "Consumer price index (demo values)",
  ref_eurchf_demo: "EUR/CHF rates (demo values)",
};

/** "s4_2" → "§4.2" (clause ids in the dataset); anything else as it is. */
export const clauseSection = (itemId: string) => (/^s\d/.test(itemId) ? `§${itemId.slice(1).replace(/_/g, ".")}` : itemId);

/** "mail_main_street_2025_04_03" → "Email of 3 Apr 2025" */
function emailLabel(ref: string) {
  const m = ref.match(/(\d{4})_(\d{2})_(\d{2})$/);
  return m ? `Email of ${formatDate(`${m[1]}-${m[2]}-${m[3]}`)} ${m[1]}` : "Email";
}

function toEvidence(refs: BeEvidenceRef[], docs: Map<string, BeDocumentSummary>): EvidenceRef[] {
  const seen = new Set<string>();
  const out: EvidenceRef[] = [];
  for (const r of refs) {
    const key = `${r.kind}|${r.document_id ?? ""}|${r.item_id ?? ""}|${r.ref ?? ""}|${r.cost_ref ?? ""}|${r.transaction_id ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const doc = r.document_id ? docs.get(r.document_id) : undefined;
    const docLabel = doc ? (doc.type === "INVOICE" && doc.document_number ? `Invoice ${doc.document_number}` : documentWord[doc.type]) : r.document_id;
    const label =
      r.kind === "CLAUSE" && r.item_id
        ? `${docLabel ?? "Contract"} ${clauseSection(r.item_id)}`
        : r.kind === "EMAIL"
          ? emailLabel(r.ref ?? "")
          : r.kind === "REFERENCE_VALUE"
            ? (referenceName[r.ref ?? ""] ?? r.ref ?? "Reference values")
            : r.kind === "COST_ITEM"
              ? `What you usually pay (${r.cost_ref})`
              : r.kind === "TRANSACTION"
                ? "Bank statement"
                : (docLabel ?? r.ref ?? r.kind.toLowerCase());
    out.push({
      kind: evidenceKind[r.kind],
      doc_id: r.document_id ?? undefined,
      item_id: r.item_id ?? undefined,
      ref: r.ref ?? undefined,
      label,
    });
  }
  return out;
}

const actionStatus: Record<BeRecommendation["status"], RecommendedAction["status"]> = {
  PENDING: "pending",
  APPROVED_EXECUTED: "approved",
  EXECUTED: "approved",
  REJECTED: "rejected",
  BLOCKED: "blocked",
  STALE: "stale",
  SUPERSEDED: "stale",
};
const actionLane: Record<BeLane, RecommendedAction["lane"]> = {
  NEEDS_APPROVAL: "needs_approval",
  NOT_ALLOWED: "not_allowed",
  DONE_WITHIN_MANDATE: "done",
};

const isProof = (r: BeRecommendation): r is BeRecommendation<BeProofSubject> =>
  r.subject.kind === "KEEP_PAYMENT" || r.subject.kind === "UPDATE_RECURRING_AMOUNT" || r.subject.kind === "SEND_LETTER";
const isCash = (r: BeRecommendation): r is BeRecommendation<BeCashSubject> => !isProof(r);

/** A Proof recommendation (KEEP_PAYMENT, UPDATE_RECURRING_AMOUNT, SEND_LETTER) as a prepared action. */
export function toAction(r: BeRecommendation): RecommendedAction | null {
  if (!isProof(r)) return null;
  const s = r.subject;
  const base = { id: r.id, lane: actionLane[r.lane], status: actionStatus[r.status], detail: r.explanation.text };
  switch (s.kind) {
    case "KEEP_PAYMENT":
      return {
        ...base,
        type: "keep_payment",
        title: `Keep paying ${formatEur(eur(s.amount))} instead of ${formatEur(eur(s.billed_amount))}${s.effective_from ? ` from ${formatDate(s.effective_from)}` : ""}`,
      };
    case "UPDATE_RECURRING_AMOUNT":
      return {
        ...base,
        type: "update_recurring_amount",
        title: `Pay ${formatEur(eur(s.to_amount))} instead of ${formatEur(eur(s.from_amount))} from ${formatDate(s.effective_from)}`,
      };
    case "SEND_LETTER":
      return {
        ...base,
        type: "send_letter",
        title: `Send a letter to ${s.counterparty}, in German`,
        letter: { to: s.counterparty, subject: s.letter_subject, body: s.draft, language: "de", source: s.draft_source === "MODEL" ? "model" : "template" },
      };
  }
}

/** Tool names for the icons; the scripted investigation leaves `tool` empty, so read it from the label. */
const TOOLS: ToolName[] = ["search_inbox", "search_documents", "read_clause", "get_reference_index", "get_price_list", "compute"];
function toolOf(tool: string | null, label: string): ToolName | undefined {
  if (tool && (TOOLS as string[]).includes(tool)) return tool as ToolName;
  if (/inbox|email/i.test(label)) return "search_inbox";
  if (/^Reading §|clause/i.test(label)) return "read_clause";
  if (/^Looking up|index for/i.test(label)) return "get_reference_index";
  if (/price list/i.test(label)) return "get_price_list";
  if (/^Looking through|document/i.test(label)) return "search_documents";
  if (/^Index moved|surcharge|^Change of|paid in the next/i.test(label)) return "compute";
  return undefined;
}

/**
 * "How Vero found out" from a Proof run's trace: each TOOL_CALL is a step (with the result of the
 * TOOL_RESULT after it), under the hypothesis of the INVESTIGATE before it ("H1 · …").
 */
export function traceFromRun(run: BeRun): TraceStep[] {
  const steps: TraceStep[] = [];
  let hypothesis: string | undefined;
  for (const t of [...run.trace].sort((a, b) => a.seq - b.seq)) {
    if (t.kind === "INVESTIGATE" && !t.label.startsWith("Vero's note")) hypothesis = t.label;
    else if (t.kind === "TOOL_CALL") steps.push({ step: steps.length + 1, hypothesis, tool: toolOf(t.tool, t.label), label: t.label });
    else if (t.kind === "TOOL_RESULT" && steps.length && !steps[steps.length - 1].result) steps[steps.length - 1].result = t.label;
  }
  return steps;
}

/** Preloaded findings have no run: their search list is all there is. */
export const traceFromSearched = (searched: string[]): TraceStep[] =>
  searched.map((label, i) => ({ step: i + 1, tool: toolOf(null, label), label }));

export interface FindingContext {
  recommendations: Map<string, BeRecommendation>;
  runs: Map<string, BeRun>;
  documents: Map<string, BeDocumentSummary>;
}

/** GET /findings (ProofFinding) → the UI finding, with its actions and its trace. */
export function toFinding(f: BeFinding, ctx: FindingContext): Finding {
  const inv = f.investigation;
  const prop = f.propagation;
  const run = f.run_id ? ctx.runs.get(f.run_id) : undefined;
  const history = f.history.filter((h) => h.classification).map((h) => classification(h.classification!, false));
  const now = classification(f.classification, f.escalated);
  if (history.length) history[history.length - 1] = now;
  const disputed = prop?.disputed ?? inv?.disputed;
  const justified = inv?.justified ?? prop?.current_amount;
  const billed = inv?.billed ?? f.impact?.billed;
  const resolved = f.status === "RESOLVED" ? f.history.findLast((h) => h.status === "RESOLVED")?.at : undefined;

  return {
    finding_id: f.id,
    title: f.title,
    status: findingStatus(f.status),
    classification: now,
    classification_history: history,
    vendor: f.counterparty,
    cost_ref: f.cost_ref,
    source_doc_id: f.document_id,
    run_id: f.run_id,
    quiet: f.quiet,
    in_digest: f.in_digest,
    line_item: {
      description: f.differences[0]?.description ?? f.title,
      previous_value: f.impact?.expected != null ? eur(f.impact.expected) : null,
      current_value: f.impact?.billed != null ? eur(f.impact.billed) : null,
      currency: f.impact?.currency ?? "EUR",
      unit: f.kind === "RECURRING_AMOUNT" ? "month" : "invoice",
      effective_from: f.period_from,
      monthly_variance: f.impact ? eur(f.impact.delta) : 0,
      variance_pct: f.impact?.bps != null ? pct(f.impact.bps) : null,
    },
    differences: f.differences.map((d) => ({ description: d.description, explained: d.explained })),
    investigation: inv
      ? {
          mode: inv.mode === "AGENT" ? "agent" : "fallback",
          formula: toFormula(inv.formula_id, inv.inputs, inv.outputs),
          billed: inv.billed != null ? eur(inv.billed) : undefined,
          justified: inv.justified != null ? eur(inv.justified) : undefined,
          disputed: inv.disputed != null ? eur(inv.disputed) : undefined,
          searched: inv.searched,
          problems: inv.problems,
          note: inv.note ?? undefined,
        }
      : undefined,
    investigation_trace: run ? traceFromRun(run) : traceFromSearched(inv?.searched ?? []),
    financial_impact:
      disputed != null && justified != null && billed != null
        ? {
            billed: eur(billed),
            justified: eur(justified),
            disputed_monthly: eur(disputed),
            disputed_annual: prop?.disputed_annual != null ? eur(prop.disputed_annual) : undefined,
            disputed_in_window: prop?.disputed_in_window != null ? eur(prop.disputed_in_window) : undefined,
          }
        : undefined,
    payment: prop
      ? {
          decision: prop.decision === "KEEP" ? "keep" : prop.decision === "UPDATE" ? "update" : "paid",
          cost_ref: prop.cost_ref,
          effective_from: prop.effective_from ?? undefined,
          current_amount: prop.current_amount != null ? eur(prop.current_amount) : undefined,
          billed_amount: prop.billed_amount != null ? eur(prop.billed_amount) : undefined,
          proposed_amount: prop.proposed_amount != null ? eur(prop.proposed_amount) : undefined,
          forecast: prop.forecast
            ? {
                payments: prop.forecast.payments,
                outflow_difference: eur(prop.forecast.outflow_difference),
                lowest_before: eur(prop.forecast.lowest_before),
                lowest_after: eur(prop.forecast.lowest_after),
              }
            : undefined,
        }
      : undefined,
    evidence_refs: toEvidence([...f.evidence_refs, ...f.differences.flatMap((d) => d.evidence)], ctx.documents),
    recommended_actions: f.recommendation_ids
      .map((id) => ctx.recommendations.get(id))
      .flatMap((r) => (r ? [toAction(r)] : []))
      .filter((a): a is RecommendedAction => a !== null),
    created_at: f.created_at,
    resolved_at: resolved,
  };
}

const actionType: Partial<Record<BeRecommendation["kind"], RecommendedAction["type"]>> = {
  KEEP_PAYMENT: "keep_payment",
  UPDATE_RECURRING_AMOUNT: "update_recurring_amount",
  SEND_LETTER: "send_letter",
};

export function toDigest(d: BeDigest): Digest {
  return {
    as_of: d.as_of,
    counts: d.counts,
    changes: d.changes.map((c) => ({
      finding_id: c.finding_id,
      title: c.title,
      classification: classification(c.classification, c.escalated),
      quiet: c.quiet,
      investigation_mode: c.investigation_mode === "AGENT" ? "agent" : c.investigation_mode === "FALLBACK" ? "fallback" : undefined,
      reason: c.reason,
      actions: c.actions.flatMap((a) => {
        const type = actionType[a.kind];
        return type ? [{ id: a.recommendation_id, type, text: a.text }] : [];
      }),
    })),
    routine_checks: d.routine_checks.map((r) => ({
      cost_ref: r.cost_ref,
      counterparty: r.counterparty,
      date: r.date,
      paid: eur(r.paid),
      expected: eur(r.expected),
      currency: r.currency,
      eur_amount: r.eur_amount != null ? eur(r.eur_amount) : undefined,
      doc_id: r.document_id,
    })),
  };
}

/** GET /runs/{id} for an upload: done, and its newest readable line while it works. */
export function toUploadRun(r: BeRun): UploadRun {
  const done = r.status === "SUCCEEDED" || r.status === "FAILED";
  const summary = r.trace.findLast((t) => t.kind === "SUMMARY")?.label;
  return {
    run_id: r.run_id,
    status: r.status === "SUCCEEDED" ? "succeeded" : r.status === "FAILED" ? "failed" : "running",
    finding_ids: r.finding_ids,
    latest: done ? undefined : (r.trace.at(-1)?.label ?? r.trigger.label),
    headline: r.result?.headline?.text ?? summary,
  };
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
    needsOwner: !f.quiet && f.status !== "RESOLVED",
    deltaEur: f.impact?.delta != null ? eur(f.impact.delta) : undefined,
    evidence: [...new Set([...f.evidence_refs, ...f.differences.flatMap((d) => d.evidence)].map((e) => evidenceWord[e.kind]).filter(Boolean))],
  };
}

const mockEvidence: Record<EvidenceRef["kind"], string> = {
  document: "documents",
  clause: "your contract",
  invoice_line: "the invoice",
  price_list_item: "your price list",
  transaction: "your bank statement",
  email: "your emails",
  cost_item: "your usual costs",
  reference: "official reference values",
};

/** The mock's finding, reduced the same way. */
export function mockToCostCheck(f: Finding): CostCheck {
  const outcome: CostCheck["outcome"] =
    f.classification === "verified" ? "matches" : f.classification === "conflict" ? "conflict" : "needs_evidence";
  return {
    id: f.finding_id,
    title: f.title,
    counterparty: f.vendor,
    outcome,
    needsOwner: !f.quiet && f.status !== "resolved",
    deltaEur: f.line_item.monthly_variance,
    evidence: [...new Set(f.evidence_refs.map((e) => mockEvidence[e.kind]).filter(Boolean))],
  };
}
