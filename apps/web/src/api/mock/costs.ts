/**
 * Vero, the cost check, in mock mode. Recurring costs come from the Pizzeria Amalfi bank statement;
 * the rent notice and the lease are demo documents. All numbers come from engine.ts or the statement parser.
 *
 * Story
 *  - Known at the start: the bank statement only.
 *  - "Rent increase notice arrives": Main Street Real Estate asks for €4,104 instead of €3,800 from 1 June.
 *    With the lease on file (uploaded on Contract check) Vero reads §5 (index rent), gets the index change and
 *    computes the justified rent → "Conflicts with contract". Without the lease it stops at "Needs evidence"
 *    and asks for it; uploading the lease later finishes the check.
 */
import type {
  Classification,
  CostMemoryRow,
  DigestRow,
  DraftMessage,
  Finding,
  FindingStatus,
  KnownDocument,
  RecommendedAction,
  SourceDoc,
  TraceStep,
} from "../types";
import { mockBus } from "../mockBus";
import { cpiCappedIndexation, fxToEur, variance } from "../../features/vero/engine";
import { clone, delay, log, model, nowIso, status } from "./runtime";
import { type Category, monthlyTotals, round2, TXNS } from "./statement";

const AS_OF = "2025-04-14";
const seen = TXNS.filter((t) => t.date < AS_OF);

const eur = (v: number) => new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR" }).format(v);
const de = (v: number) => `${v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
const deDate = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;

// ───────────── Inputs ─────────────

export const LANDLORD = "Main Street Real Estate GmbH";
const RENT = -seen.filter((t) => t.category === "rent").at(-1)!.amountEur; // 3800, from the statement
const NOTICE_PCT = 8;
const NOTICED = round2(RENT * (1 + NOTICE_PCT / 100)); // 4104
const VPI_PCT = 2.2; // demo dataset value, labelled as such in the UI
const EFFECTIVE = "2025-06-01";

export const rentCheck = cpiCappedIndexation(RENT, VPI_PCT, NOTICED);
const rentVariance = variance(RENT, NOTICED);

const STATEMENT_ID = "stmt_2025";
const NOTICE_ID = "ntc_rent_2025_04";
export const LEASE_ID = "lease_main_street";
const FINDING_ID = "fnd_2025_04_rent_01";

// ───────────── State ─────────────

let docs: KnownDocument[] = [];
let findings: Finding[] = [];
let onRentApproved: (rent: number) => Promise<void> = async () => {};
export const setRentApprovedHandler = (fn: (rent: number) => Promise<void>) => {
  onRentApproved = fn;
};

export function resetCosts() {
  docs = [
    {
      doc_id: STATEMENT_ID,
      kind: "statement",
      label: "Bank statement 2025",
      note: `1 Jan – 13 Apr · ${seen.length} transactions`,
      source: "history",
      added_at: "2025-04-14T06:00:00Z",
    },
  ];
  findings = [];
}
resetCosts();

export const hasLease = () => docs.some((d) => d.doc_id === LEASE_ID);

// ───────────── Cost Memory (from the statement) ─────────────

const MONTHS = ["2025-01", "2025-02", "2025-03", "2025-04"];
const MEMORY: { label: string; vendor: string; category: Category }[] = [
  { label: "Rent", vendor: LANDLORD, category: "rent" },
  { label: "Energy & gas", vendor: "City Energy & Utilities AG", category: "utilities" },
  { label: "Social security", vendor: "Techniker Krankenkasse", category: "social_security" },
  { label: "Food & ingredients", vendor: "Asimex Wholesale Ltd", category: "food" },
  { label: "Wine & beverages", vendor: "Fattoria La Vino GmbH", category: "wine" },
  { label: "Fresh produce", vendor: "Central Wholesale Market", category: "produce" },
  { label: "CHF transfer (in EUR)", vendor: "Luigi Amalfi, Zurich", category: "fx_transfer" },
];

export function getMemory(): CostMemoryRow[] {
  return MEMORY.map((m) => {
    const totals = monthlyTotals(AS_OF, m.category);
    return {
      label: m.label,
      vendor: m.vendor,
      months: MONTHS.map((p) => ({ period: p, value: -(totals.get(p) ?? 0), partial: p === "2025-04" })),
    };
  });
}

// ───────────── Weekly digest: changes explained without bothering the owner ─────────────

function monthOf(category: Category, period: string) {
  return seen.filter((t) => t.category === category && t.date.startsWith(period));
}

export function getDigest(): DigestRow[] {
  const [chfFeb] = monthOf("fx_transfer", "2025-02");
  const [chfMar] = monthOf("fx_transfer", "2025-03");
  const chf = variance(-chfFeb.amountEur, -chfMar.amountEur);

  const taxFeb = -monthOf("wage_tax", "2025-02").concat(monthOf("trade_tax", "2025-02")).reduce((s, t) => s + t.amountEur, 0);
  const wage = -monthOf("wage_tax", "2025-03")[0].amountEur;
  const income = -monthOf("income_tax", "2025-03")[0].amountEur;

  const wineFeb = monthOf("wine", "2025-02");
  const wineMar = monthOf("wine", "2025-03");
  const sum = (l: typeof seen) => -round2(l.reduce((s, t) => s + t.amountEur, 0));
  const wine = variance(sum(wineFeb), sum(wineMar));
  const avg = (l: typeof seen) => round2(sum(l) / l.length);

  return [
    {
      id: "dg_chf_2025_03",
      classification: "verified",
      title: `CHF transfer, March: ${eur(-chfMar.amountEur)} (${chf.pct > 0 ? "+" : ""}${chf.pct}%)`,
      detail: "Same 4,166.67 CHF as every month. Only the exchange rate changed.",
      doc_id: STATEMENT_ID,
      calc: [
        `February: ${chfFeb.amountOriginal * -1} CHF × ${chfFeb.fxRate} = ${eur(fxToEur(-chfFeb.amountOriginal, chfFeb.fxRate!))}`,
        `March:    ${chfMar.amountOriginal * -1} CHF × ${chfMar.fxRate} = ${eur(fxToEur(-chfMar.amountOriginal, chfMar.fxRate!))}`,
      ],
    },
    {
      id: "dg_tax_2025_03",
      classification: "verified",
      title: `Tax Office Frankfurt, March: ${eur(wage + income)} (February: ${eur(taxFeb)})`,
      detail: "Quarterly income-tax prepayment, due every March, June, September and December.",
      doc_id: STATEMENT_ID,
      calc: [`Wage tax ${eur(wage)} + income-tax prepayment Q1 ${eur(income)} = ${eur(wage + income)}`],
    },
    {
      id: "dg_wine_2025_03",
      classification: "verified",
      title: `Fattoria La Vino, March: ${eur(sum(wineMar))} (+${wine.pct}%)`,
      detail: "More deliveries, not higher prices.",
      doc_id: STATEMENT_ID,
      calc: [
        `February: ${wineFeb.length} invoices, average ${eur(avg(wineFeb))}`,
        `March:    ${wineMar.length} invoices, average ${eur(avg(wineMar))}`,
      ],
    },
  ];
}

// ───────────── Source documents ─────────────

const rentLines = seen.filter((t) => t.category === "rent");

const DOCS: Record<string, SourceDoc> = {
  [STATEMENT_ID]: {
    doc_id: STATEMENT_ID,
    kind: "Statement",
    title: "Kontoauszug 2025 · DE89 3704 0044 0532 0130 00",
    meta: "Bank feed · rent payments to Main Street Real Estate GmbH",
    page: 1,
    lines: [
      { text: "Buchung    Verwendungszweck                          Betrag", muted: true },
      ...rentLines.map((t, i) => ({
        text: `${deDate(t.date)}  ${t.reference}  ${de(t.amountEur)}`,
        ...(i === rentLines.length - 1
          ? { en: "Rent paid every month since January: always €3,800.00", box: { label: "Paid today", tone: "neutral" as const } }
          : { muted: true }),
      })),
    ],
  },
  [NOTICE_ID]: {
    doc_id: NOTICE_ID,
    kind: "Notice",
    title: "Mieterhöhung_Pizzeria_Amalfi.pdf",
    meta: `${LANDLORD} · received by e-mail on 14.04.2025 · demo document`,
    page: 1,
    lines: [
      { text: `${LANDLORD} · Hausverwaltung · Frankfurt am Main`, muted: true },
      { text: "Gewerbefläche Pizzeria Amalfi, Mieter: Vincenzo Amalfi", muted: true },
      { text: "Mitteilung über die Anpassung der Miete", strong: true },
      {
        text: `Ab dem ${deDate(EFFECTIVE)} beträgt die monatliche Miete ${de(NOTICED)} statt bisher ${de(RENT)} (+${NOTICE_PCT} %).`,
        en: `From 1 June 2025 the monthly rent is ${eur(NOTICED)} instead of ${eur(RENT)} (+${NOTICE_PCT}%).`,
        box: { label: "Asked for", tone: "billed" },
      },
      {
        text: "Grund: gestiegene Bewirtschaftungskosten.",
        en: "Reason: higher operating costs. No index and no lease clause are cited.",
        box: { label: "Reason given", tone: "neutral" },
      },
      { text: "Bitte passen Sie Ihren Dauerauftrag entsprechend an.", en: "Please change your standing order.", muted: true },
    ],
  },
  [LEASE_ID]: {
    doc_id: LEASE_ID,
    kind: "Lease",
    title: "Gewerbemietvertrag (Mietvertrag) · Pizzeria Amalfi",
    meta: `${LANDLORD} and Vincenzo Amalfi · from Contract check · demo document`,
    page: 2,
    lines: [
      { text: "§2 Mietbeginn: 01.01.2023, unbefristet.", muted: true, en: "Lease start 1 Jan 2023, open-ended." },
      { text: `§3 Miete. Die monatliche Miete beträgt ${de(RENT)}, fällig am 1. eines Monats.`, muted: true, en: `Rent ${eur(RENT)} a month, due on the 1st.` },
      {
        text:
          "§5 Indexmiete. Die Miete ändert sich im gleichen prozentualen Verhältnis wie der vom Statistischen Bundesamt ermittelte Verbraucherpreisindex für Deutschland (VPI).",
        en: "§5 Index rent: the rent changes by the same percentage as the German consumer price index (VPI).",
        box: { label: "§5 basis", tone: "basis" },
      },
      {
        text: "§5.2 Eine Anpassung ist frühestens nach einem Jahr und nur durch schriftliche Erklärung unter Angabe der Indexänderung möglich.",
        en: "At most once a year, in writing, stating the index change.",
        box: { label: "§5.2 form", tone: "basis" },
      },
      { text: "§12 Kündigung: 6 Monate zum Quartalsende.", muted: true, en: "Notice: 6 months to the end of a quarter." },
    ],
  },
};

export function getDocument(id: string): SourceDoc {
  const d = DOCS[id];
  if (!d) throw new Error(`Document ${id} not found`);
  return clone(d);
}

export const getDocuments = () => clone(docs);
export const getFindings = () => clone(findings);

// ───────────── The rent check ─────────────

const LETTER: DraftMessage = {
  kind: "counter_proposal",
  to: "hausverwaltung@mainstreet-realestate.example",
  subject: "Ihre Mitteilung vom 14.04.2025 – Miete Gewerbefläche Pizzeria Amalfi",
  body: `Sehr geehrte Damen und Herren,

vielen Dank für Ihre Mitteilung vom 14.04.2025. Sie kündigen eine Miete von ${de(NOTICED)} statt ${de(RENT)} (+${NOTICE_PCT} %) ab dem ${deDate(EFFECTIVE)} an.

Nach § 5 unseres Mietvertrags (Indexmiete) ändert sich die Miete nur im Verhältnis zum Verbraucherpreisindex für Deutschland (VPI). Die maßgebliche Indexänderung beträgt ${VPI_PCT.toLocaleString("de-DE")} %. Daraus ergibt sich eine Miete von höchstens ${de(rentCheck.justified)}.

Ab dem ${deDate(EFFECTIVE)} überweisen wir daher ${de(rentCheck.justified)} monatlich. Bitte nennen Sie uns die vertragliche Grundlage für den darüber hinausgehenden Betrag von ${de(rentCheck.disputedMonthly)}.

Mit freundlichen Grüßen
Vincenzo Amalfi
Pizzeria Amalfi`,
  english: {
    subject: "Your notice of 14 April 2025 – rent for the Pizzeria Amalfi premises",
    body: `Dear Sir or Madam,

Thank you for your notice of 14 April 2025. You announce a rent of ${eur(NOTICED)} instead of ${eur(RENT)} (+${NOTICE_PCT}%) from 1 June 2025.

Under §5 of our lease (index rent) the rent only changes in line with the German consumer price index (VPI). The relevant index change is ${VPI_PCT}%, which gives a rent of at most ${eur(rentCheck.justified)}.

From 1 June 2025 we will therefore transfer ${eur(rentCheck.justified)} a month. Please tell us the contractual basis for the remaining ${eur(rentCheck.disputedMonthly)}.

Kind regards
Vincenzo Amalfi
Pizzeria Amalfi`,
  },
};

function baseFinding(): Finding {
  return {
    finding_id: FINDING_ID,
    status: "ingested",
    classification: "unexplained",
    classification_history: [],
    vendor: LANDLORD,
    billing_period: "2025-06",
    source_doc_id: NOTICE_ID,
    line_item: {
      description: "Rent, commercial premises (Gewerbemiete)",
      previous_value: RENT,
      current_value: NOTICED,
      currency: "EUR",
      unit: "month",
      effective_from: EFFECTIVE,
      monthly_variance: rentVariance.monthly,
      variance_pct: rentVariance.pct,
    },
    investigation_trace: [],
    evidence_refs: [
      { type: "statement", doc_id: STATEMENT_ID, snippet: `Rent ${eur(RENT)} every month since January` },
      { type: "notice", doc_id: NOTICE_ID, page: 1, snippet: `${de(NOTICED)} statt bisher ${de(RENT)} (+${NOTICE_PCT} %)` },
    ],
    propagation: [],
    recommended_actions: [],
    created_at: nowIso(),
  };
}

const ACTIONS: RecommendedAction[] = [
  {
    type: "send_clarification_letter",
    title: "Send a clarification letter to the landlord, in German",
    detail: "Cites §5 of the lease and the index change. Pays the justified rent, so you are never in arrears.",
    requires_approval: true,
    execution: "simulated",
    language: "de",
    draft: LETTER,
  },
  {
    type: "update_standing_order",
    title: `Set the rent standing order to ${eur(rentCheck.justified)} from 1 June`,
    detail: `Instead of ${eur(NOTICED)}. The disputed ${eur(rentCheck.disputedMonthly)} a month waits for the landlord's answer.`,
    requires_approval: true,
    execution: "simulated",
  },
  {
    type: "update_forecast",
    title: `Use ${eur(rentCheck.justified)} rent in your 13-week forecast`,
    detail: `+${eur(rentCheck.justifiedIncrease)} a month from June compared with today.`,
    requires_approval: true,
    execution: "simulated",
  },
];

let current: Finding | null = null;

function publish(patch: Partial<Finding>) {
  if (!current) return;
  current = { ...current, ...patch };
  findings = [current, ...findings.filter((f) => f.finding_id !== current!.finding_id)];
  mockBus.emit({ type: "finding.updated", data: clone(current) });
}

function classify(c: Classification, statusNext: FindingStatus) {
  if (!current) return;
  publish({
    classification: c,
    classification_history: [...current.classification_history, c],
    status: statusNext,
  });
}

async function step(s: Omit<TraceStep, "step" | "model">, ms: number) {
  if (!current) return;
  await delay(ms);
  const full: TraceStep = { ...s, step: current.investigation_trace.length + 1, model: model(`${FINDING_ID}-${s.tool}-${s.hypothesis}`) };
  current = { ...current, investigation_trace: [...current.investigation_trace, full] };
  findings = [current, ...findings.filter((f) => f.finding_id !== current!.finding_id)];
  mockBus.emit({ type: "finding.step", data: { finding_id: FINDING_ID, step: full } });
}

async function checkAgainstLease(speed: number) {
  await step(
    {
      hypothesis: "The lease allows a rent change",
      tool: "search_documents",
      input: { query: "Mietvertrag Main Street Real Estate", kind: "lease" },
      result: `1 match: ${LEASE_ID} (uploaded on Contract check).`,
      evidence_ref: `${LEASE_ID}#p1`,
    },
    1300 / speed,
  );
  await step(
    {
      hypothesis: "Find the rule for rent changes",
      tool: "read_clause",
      input: { doc_id: LEASE_ID, section: "§5" },
      result: "Index rent: the rent changes by the same percentage as the German consumer price index (VPI), at most once a year, in writing, stating the index change.",
      evidence_ref: `${LEASE_ID}#p2`,
    },
    1500 / speed,
  );
  await step(
    {
      hypothesis: "Get the index change",
      tool: "get_reference_index",
      input: { name: "VPI_Germany", period: "2024-03..2025-03" },
      result: `+${VPI_PCT}% (demo dataset value)`,
      evidence_ref: "ref_vpi_2025_03",
    },
    1300 / speed,
  );
  await step(
    {
      hypothesis: "Compute the rent the lease allows",
      tool: "compute",
      input: { formula_id: "cpi_capped_indexation", base: RENT, cap_pct: VPI_PCT, billed: NOTICED },
      result: `Justified rent ${eur(rentCheck.justified)}. Disputed ${eur(rentCheck.disputedMonthly)} a month.`,
      evidence_ref: null,
    },
    1200 / speed,
  );
  await delay(500 / speed);
  publish({
    evidence_request: undefined,
    evidence_refs: [
      ...current!.evidence_refs.filter((e) => e.type !== "lease"),
      { type: "lease", doc_id: LEASE_ID, page: 2, clause: "§5", snippet: "im gleichen prozentualen Verhältnis wie der … Verbraucherpreisindex (VPI)" },
    ],
    financial_impact: {
      justified_value: rentCheck.justified,
      justified_increase_monthly: rentCheck.justifiedIncrease,
      disputed_amount_monthly: rentCheck.disputedMonthly,
      disputed_amount_annual: rentCheck.disputedAnnual,
      assumption: "Disputed amount assumes the asked rent would be paid for 12 months",
    },
    propagation: [
      { target: "standing_order", id: "so_rent_main_street", old: RENT, new: rentCheck.justified, note: "Pay the justified rent; the disputed part waits for clarification" },
      { target: "forecast", id: "fc_rent", delta_monthly: rentCheck.justifiedIncrease, from: EFFECTIVE, note: "Rent line in the 13-week forecast" },
    ],
    recommended_actions: ACTIONS,
  });
  classify("conflict", "classified");
  await delay(400 / speed);
  publish({ status: "pending_approval" });
  status("costs", "Rent increase conflicts with your lease. Actions prepared.", false);
  await log("agent", `Rent notice ${eur(RENT)} → ${eur(NOTICED)}: conflicts with lease §5, justified ${eur(rentCheck.justified)}, disputed ${eur(rentCheck.disputedAnnual)} a year`, {
    agent: "costs",
    autonomy: "approval_required",
    execution: "simulated",
    model: model(`${FINDING_ID}-classified`),
  });
}

/** Demo event: the landlord's notice arrives by e-mail. */
export async function runRentNotice(speed = 1) {
  const notice: KnownDocument = {
    doc_id: NOTICE_ID,
    kind: "notice",
    label: "Rent notice, Main Street Real Estate",
    note: `+${NOTICE_PCT}% from 1 June`,
    source: "inbox",
    added_at: nowIso(),
  };
  docs = [...docs.filter((d) => d.doc_id !== NOTICE_ID), notice];
  mockBus.emit({ type: "document.added", data: notice });
  status("costs", "New document from the landlord. Reading it…", true);

  current = baseFinding();
  publish({ status: "ingested" });
  await delay(900 / speed);
  publish({ status: "extracted" });
  await delay(900 / speed);
  classify("unexplained", "checked");
  await delay(900 / speed);
  publish({ status: "investigating" });
  status("costs", "Rent goes up 8%. Looking for the reason…", true);

  await step(
    {
      hypothesis: "The notice gives a valid reason",
      tool: "read_document",
      input: { doc_id: NOTICE_ID },
      result: `Rent ${eur(RENT)} → ${eur(NOTICED)} (+${NOTICE_PCT}%) from 1 June 2025. Reason given: "higher operating costs". No index and no lease clause cited.`,
      evidence_ref: `${NOTICE_ID}#p1`,
    },
    1600 / speed,
  );

  if (hasLease()) {
    await checkAgainstLease(speed);
    return;
  }

  await step(
    {
      hypothesis: "The lease allows a rent change",
      tool: "search_documents",
      input: { query: "Mietvertrag Main Street Real Estate", kind: "lease" },
      result: `No lease on file. The bank statement shows rent of ${eur(RENT)} every month since January, nothing else.`,
      evidence_ref: `${STATEMENT_ID}#rent`,
    },
    1400 / speed,
  );
  await delay(400 / speed);
  publish({
    status: "classified",
    evidence_request: {
      message: "Vero can't tell if this increase is allowed without your lease. Nothing is changed until then.",
      action_label: "Upload your lease on Contract check",
      href: "/contract",
    },
  });
  status("costs", "Needs your lease to finish the rent check.", false);
  await log("agent", `Rent notice ${eur(RENT)} → ${eur(NOTICED)}: needs evidence, asked the owner for the lease`, {
    agent: "costs",
    autonomy: "analysis",
    model: model(`${FINDING_ID}-needs-evidence`),
  });
}

/** Called by Contract check when a lease is uploaded. Returns how many open checks it restarted. */
export async function registerLease(): Promise<number> {
  const lease: KnownDocument = {
    doc_id: LEASE_ID,
    kind: "lease",
    label: "Lease, Main Street Real Estate",
    note: "§5 index rent (VPI)",
    source: "contract_check",
    added_at: nowIso(),
  };
  docs = [...docs.filter((d) => d.doc_id !== LEASE_ID), lease];
  mockBus.emit({ type: "document.added", data: lease });

  const waiting = current && current.evidence_request && current.status !== "resolved";
  if (waiting) {
    publish({ status: "investigating" });
    status("costs", "Lease received. Finishing the rent check…", true);
    void checkAgainstLease(1);
    return 1;
  }
  return 0;
}

export async function approveFinding(id: string, actions: RecommendedAction["type"][]): Promise<Finding> {
  await delay(400);
  if (!current || current.finding_id !== id) throw new Error(`Finding ${id} not found`);
  publish({ status: "resolved", resolved_at: nowIso(), recommended_actions: current.recommended_actions.filter((a) => actions.includes(a.type)) });
  await log("owner", `Approved ${actions.length} rent actions (simulated): ${actions.join(", ")}`, { agent: "costs", autonomy: "approval_required", execution: "simulated" });
  if (actions.includes("update_forecast")) await onRentApproved(rentCheck.justified);
  return clone(current);
}
