/**
 * Vero, the cost check, in mock mode: the reference demo of docs/PROOF.md §7, so mock and live
 * tell the same story with the same numbers. Recurring costs come from the Pizzeria Amalfi bank
 * statement; the lease, the landlord's letter and the rent adjustment are the demo documents.
 * All rent numbers come from engine.ts, the words from features/vero/story.ts (as in live mode).
 *
 * Story
 *  - Known at the start: the bank statement, the lease (§4.2 index clause), the contracts and price
 *    lists. Three supplier changes were explained by the evidence and are handled quietly (§7.2).
 *  - The rent adjustment is uploaded (or arrives via the demo panel): Main Street Real Estate bills
 *    €4,104 instead of €3,800 from 1 June. Vero finds the landlord's letter in the inbox, reads lease
 *    §4.2, looks up the price index and computes: 3.06 % < 5 %, so the rent stays €3,800 → conflict,
 *    with "keep paying €3,800" and a German letter prepared for the owner's OK (§7.1).
 */
import type { Classification, CostMemoryRow, Digest, DocumentRun, DraftMessage, Finding, FindingStatus, KnownDocument, RecommendedAction, RoutineCheck, SourceDoc, TraceStep } from "../types";
import { ApiError } from "../client";
import { mockBus } from "../mockBus";
import { vpiThresholdIndexation } from "../../features/vero/engine";
import { indexRentVerdict, paymentPropagation } from "../../features/vero/story";
import { clone, delay, log, model, nowIso, status } from "./runtime";
import { type Category, monthlyTotals, TXNS } from "./statement";

const AS_OF = "2025-04-14";
const seen = TXNS.filter((t) => t.date < AS_OF);

const eur = (v: number) => new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR" }).format(v);
const chf = (v: number) => new Intl.NumberFormat("en-GB", { style: "currency", currency: "CHF" }).format(v);

// ───────────── Inputs (docs/PROOF.md §4, §7.1) ─────────────

export const LANDLORD = "Main Street Real Estate GmbH";
const RENT = -seen.filter((t) => t.category === "rent").at(-1)!.amountEur; // 3800, from the statement
const BILLED = 4104; // the rent adjustment of 30 Apr 2025
const VPI_BASE = 117.6; // Jan 2024 (demo value)
const VPI_NOW = 121.2; // Mar 2025 (demo value)
const THRESHOLD_BPS = 500; // lease §4.2: at least 5 %
const EFFECTIVE = "2025-06-01";
const PAYMENTS = ["RENT-06", "RENT-07", "RENT-08"]; // inside the 13-week window

export const rentCheck = vpiThresholdIndexation(RENT, VPI_BASE, VPI_NOW, THRESHOLD_BPS, BILLED);

const STATEMENT_ID = "stmt_2025";
const UPLOAD_ID = "doc_rent_adjustment_2025_06";
const LETTER_ID = "ntc_main_street_2025_04";
export const LEASE_ID = "lease_main_street_2021";
const RENT_FINDING = "fnd_rent_2025_06";
const RUN_ID = "run_rent_2025_06";

// ───────────── Documents on file ─────────────

const ON_FILE: KnownDocument[] = [
  { doc_id: STATEMENT_ID, kind: "statement", label: "Bank statement 2025", note: `1 Jan – 13 Apr · ${seen.length} transactions`, source: "history", added_at: "2025-04-14T06:00:00Z" },
  { doc_id: LEASE_ID, kind: "lease", label: "Lease, Main Street Real Estate", note: "§4.2 index clause (VPI, 5 % threshold)", source: "history", added_at: "2025-04-14T06:00:00Z" },
  { doc_id: "ctr_city_energy_2025", kind: "contract", label: "Payment plan, City Energy", note: "€650.00 a month", source: "history", added_at: "2025-04-14T06:00:00Z" },
  { doc_id: "ctr_luigi_2024", kind: "contract", label: "Partnership agreement, Luigi Amalfi", note: "4,166.67 CHF a month", source: "history", added_at: "2025-04-14T06:00:00Z" },
  { doc_id: "pl_asimex_2025_q2", kind: "contract", label: "Price list Q2 2025, Asimex", note: "from 1 Apr 2025", source: "history", added_at: "2025-04-14T06:00:00Z" },
  { doc_id: "terms_asimex_2025", kind: "contract", label: "Terms, Asimex", note: "Logistikzuschlag from 1 May 2025", source: "history", added_at: "2025-04-14T06:00:00Z" },
  { doc_id: "pl_fattoria_2025", kind: "contract", label: "Price list 2025, Fattoria La Vino", note: "from 1 Apr 2025", source: "history", added_at: "2025-04-14T06:00:00Z" },
];

// ───────────── State ─────────────

let docs: KnownDocument[] = [];
let findings: Finding[] = [];
let run: DocumentRun | null = null;

export function resetCosts() {
  docs = clone(ON_FILE);
  findings = quietFindings();
  run = null;
  current = null;
}

export const getDocuments = () => clone(docs);
export const getFindings = () => clone(findings);

// ───────────── Cost Memory (from the statement) ─────────────

// Same shape as the real GET /cost-memory: fixed costs expect last full month's amount (March),
// suppliers are checked per invoice. The CHF transfer is 4,166.67 CHF a month.
const LAST_FULL_MONTH = "2025-03";
const CHF_MONTHLY = 4166.67;
const MEMORY: { ref: string; label: string; vendor: string; category: Category; kind: "fixed" | "supplier"; contract?: boolean }[] = [
  { ref: "RENT", label: "Rent", vendor: LANDLORD, category: "rent", kind: "fixed", contract: true },
  { ref: "UTIL", label: "Energy & utilities", vendor: "City Energy & Utilities AG", category: "utilities", kind: "fixed", contract: true },
  { ref: "XFER", label: "CHF transfer", vendor: "Luigi Amalfi, Zurich", category: "fx_transfer", kind: "fixed", contract: true },
  { ref: "SOC", label: "Social security", vendor: "Techniker Krankenkasse", category: "social_security", kind: "fixed" },
  { ref: "ASIMEX", label: "Supplier", vendor: "Asimex Wholesale Ltd", category: "food", kind: "supplier" },
  { ref: "FATTORIA", label: "Supplier", vendor: "Fattoria La Vino GmbH", category: "wine", kind: "supplier" },
  { ref: "MARKET", label: "Supplier", vendor: "Central Wholesale Market", category: "produce", kind: "supplier" },
];

export function getMemory(): CostMemoryRow[] {
  return MEMORY.map((m) => {
    const march = -(monthlyTotals(AS_OF, m.category).get(LAST_FULL_MONTH) ?? 0);
    const isChf = m.category === "fx_transfer";
    return {
      cost_ref: m.ref,
      label: m.label,
      vendor: m.vendor,
      kind: m.kind,
      expected:
        m.kind === "fixed"
          ? isChf
            ? { amount: CHF_MONTHLY, currency: "CHF", eur_amount: march }
            : { amount: march, currency: "EUR" }
          : undefined,
      prices: 0,
      sources: m.contract ? ["statement", "contract"] : ["statement"],
    };
  });
}

// ───────────── Quiet changes (docs/PROOF.md §7.2), preloaded ─────────────

interface QuietCase {
  id: string;
  vendor: string;
  title: string;
  doc: string;
  date: string;
  expected: number;
  billed: number;
  reason: string;
  calc: string[];
  evidence: Finding["evidence_refs"];
}

const QUIET: QuietCase[] = [
  {
    id: "fnd_as_72217",
    vendor: "Asimex Wholesale Ltd",
    title: "New prices on AS-72217",
    doc: "inv_AS-72217",
    date: "2025-04-26",
    expected: 627.18,
    billed: 642.27,
    reason: "Every difference matches the Price list Q2 2025",
    calc: [
      "Mozzarella di Bufala Campana DOP: €12.40 → €12.90",
      "Prosciutto di Parma DOP: €21.90 → €23.40",
      "Salame Napoli: new, €15.20",
      "Pomodori Pelati San Marzano DOP, 2,5 kg: €6.80 → €7.20",
    ],
    evidence: [
      { type: "invoice", doc_id: "inv_AS-72217", label: "Invoice" },
      { type: "price_list", doc_id: "pl_asimex_2025_q2", label: "Price list" },
    ],
  },
  {
    id: "fnd_flv_94818",
    vendor: "Fattoria La Vino GmbH",
    title: "New prices on FLV-94818",
    doc: "inv_FLV-94818",
    date: "2025-04-28",
    expected: 728.29,
    billed: 753.85,
    reason: "Every difference matches the Price list 2025",
    calc: [
      "Chianti Classico DOCG 2021, 0,75 l: €8.90 → €9.40",
      "Pinot Grigio delle Venezie DOC 2023, 0,75 l: €6.20 → €6.60",
      "Primitivo di Manduria DOC 2021, 0,75 l: €9.85 → €10.45",
      "Lambrusco Grasparossa di Castelvetro DOC, 0,75 l: new, €5.85",
      "Acqua San Pellegrino, 0,75 l: €0.89 → €0.95",
    ],
    evidence: [
      { type: "invoice", doc_id: "inv_FLV-94818", label: "Invoice" },
      { type: "price_list", doc_id: "pl_fattoria_2025", label: "Price list" },
    ],
  },
  {
    id: "fnd_as_11607",
    vendor: "Asimex Wholesale Ltd",
    title: "Logistikzuschlag (€24.50) on AS-11607",
    doc: "inv_AS-11607",
    date: "2025-05-03",
    expected: 558.71,
    billed: 583.21,
    reason: "Verified: €24.50 allowed by the evidence",
    calc: ["Logistikzuschlag: €24.50, new", "Terms from 1 May 2025: €24.50 per delivery under €600.00 of goods; this one: €558.71"],
    evidence: [
      { type: "invoice", doc_id: "inv_AS-11607", label: "Invoice" },
      { type: "contract", doc_id: "terms_asimex_2025", label: "Contract" },
      { type: "email", doc_id: null, label: "Email", ref: "mail_asimex_2025_04_14" },
    ],
  },
];

function quietFindings(): Finding[] {
  return QUIET.map((q) => ({
    finding_id: q.id,
    status: "classified",
    classification: "verified",
    classification_history: ["verified"],
    vendor: q.vendor,
    title: q.title,
    source_doc_id: q.doc,
    line_item: {
      description: "Invoice total",
      previous_value: q.expected,
      current_value: q.billed,
      currency: "EUR",
      unit: "invoice",
      effective_from: q.date,
      monthly_variance: Math.round((q.billed - q.expected) * 100) / 100,
      variance_pct: Math.round(((q.billed - q.expected) / q.expected) * 10000) / 100,
    },
    investigation_trace: [],
    reason: q.reason,
    evidence_refs: q.evidence,
    propagation: [],
    recommended_actions: [],
    quiet: true,
    acknowledged: false,
    created_at: "2025-04-14T08:00:00Z",
  }));
}

// ───────────── Weekly digest (docs/PROOF.md §7.3, §7.4) ─────────────

/** The latest payment of each fixed cost with a contract, compared with it. */
function routineChecks(): RoutineCheck[] {
  const last = (c: Category) => seen.filter((t) => t.category === c).at(-1)!;
  const rent = last("rent");
  const util = last("utilities");
  const xfer = last("fx_transfer");
  const xferChf = -(xfer.amountOriginal ?? -CHF_MONTHLY);
  return [
    { cost_ref: "RENT", counterparty: LANDLORD, date: rent.date, paid: eur(-rent.amountEur), expected: eur(RENT), matches: -rent.amountEur === RENT, doc_id: LEASE_ID },
    { cost_ref: "UTIL", counterparty: "City Energy & Utilities AG", date: util.date, paid: eur(-util.amountEur), expected: eur(650), matches: -util.amountEur === 650, doc_id: "ctr_city_energy_2025" },
    {
      cost_ref: "XFER",
      counterparty: "Luigi Amalfi (Luigi's Ristorante Zurich)",
      date: xfer.date,
      paid: `${chf(xferChf)} (${eur(-xfer.amountEur)})`,
      expected: chf(CHF_MONTHLY),
      matches: Math.abs(xferChf - CHF_MONTHLY) < 0.005,
      doc_id: "ctr_luigi_2024",
    },
  ];
}

export function getDigest(): Digest {
  const open = findings.filter((f) => !f.acknowledged && f.status !== "resolved");
  const quiet = open.filter((f) => f.quiet);
  const routine = routineChecks();
  return clone({
    counts: { changes: open.length, handled_quietly: quiet.length, needs_owner: open.length - quiet.length, routine_checks: routine.filter((c) => c.matches).length },
    quiet: quiet.map((f) => {
      const q = QUIET.find((x) => x.id === f.finding_id)!;
      return { id: f.finding_id, classification: f.classification, title: `${f.vendor}: ${f.title}`, detail: f.reason, doc_id: f.source_doc_id, calc: q.calc };
    }),
    routine,
  });
}

export async function acknowledgeFinding(id: string): Promise<void> {
  await delay(250);
  const f = findings.find((x) => x.finding_id === id);
  if (!f || !f.quiet || f.acknowledged) throw new ApiError(409, `/findings/${id}/acknowledge`, "only a verified finding with nothing to decide can be acknowledged", "NOT_PENDING");
  findings = findings.map((x) => (x.finding_id === id ? { ...x, acknowledged: true, status: "resolved", resolved_at: nowIso() } : x));
  await log("owner", `Acknowledged: ${f.vendor}: ${f.title}`, { agent: "costs", autonomy: "analysis" });
}

// ───────────── Source documents (the texts of the demo PDFs) ─────────────

const LETTERHEAD = { id: "letterhead", text: "Main Street Real Estate GmbH · Hausverwaltung · Mainufer 18 · 60311 Frankfurt am Main", muted: true };
const RECIPIENT = { id: "recipient", text: "Pizzeria Amalfi · Herrn Vincenzo Amalfi · Hauptstraße 12 · 60311 Frankfurt am Main", muted: true };

const DOCS: Record<string, SourceDoc> = {
  [UPLOAD_ID]: {
    doc_id: UPLOAD_ID,
    kind: "Notice",
    title: "Mietanpassung",
    meta: `Rent adjustment · ${LANDLORD} · 30 Apr 2025 · uploaded`,
    page: 1,
    lines: [
      LETTERHEAD,
      RECIPIENT,
      { id: "place_date", text: "Frankfurt am Main, 30.04.2025", en: "Frankfurt am Main, 30 Apr 2025" },
      { id: "salutation", text: "Sehr geehrter Herr Amalfi, wie angekündigt passen wir Ihre Miete wie folgt an:", en: "Dear Mr Amalfi, as announced we adjust your rent as follows:" },
      { text: "Bisherige Miete 3.800,00 €", en: "Rent so far", box: { label: "Basis", tone: "basis" } },
      { text: "Neue Miete ab 1 Jun 2025: 4.104,00 €", en: "New rent", strong: true, box: { label: "Billed", tone: "billed" } },
      { id: "instruction", text: "Bitte passen Sie Ihren Dauerauftrag entsprechend an.", en: "Please change your standing order accordingly." },
      { id: "closing", text: "Mit freundlichen Grüßen · Main Street Real Estate GmbH · Hausverwaltung", en: "Kind regards · Main Street Real Estate GmbH · Property management", muted: true },
    ],
  },
  [LETTER_ID]: {
    doc_id: LETTER_ID,
    kind: "Notice",
    title: "Mitteilung zum Mietverhältnis",
    meta: `Notice about the lease · ${LANDLORD} · 3 Apr 2025 · attached to an email in the grease-trap thread`,
    page: 1,
    lines: [
      LETTERHEAD,
      RECIPIENT,
      { id: "place_date", text: "Frankfurt am Main, 03.04.2025", en: "Frankfurt am Main, 3 Apr 2025" },
      { id: "salutation", text: "Sehr geehrter Herr Amalfi, hiermit teilen wir Ihnen Folgendes mit:", en: "Dear Mr Amalfi, we hereby inform you as follows:" },
      {
        id: "increase",
        text: "Erhöhung der Miete um 8 % ab 01.06.2025 aufgrund gestiegener Bewirtschaftungskosten.",
        en: "Increase of the rent by 8 % from 1 Jun 2025 due to higher management costs. No index and no clause are named.",
        box: { label: "Reason given", tone: "neutral" },
      },
      { id: "follow_up", text: "Die neue Monatsmiete teilen wir Ihnen mit gesondertem Schreiben mit.", en: "We will tell you the new monthly rent in a separate letter." },
      { id: "closing", text: "Mit freundlichen Grüßen · Main Street Real Estate GmbH · Hausverwaltung", en: "Kind regards · Main Street Real Estate GmbH · Property management", muted: true },
    ],
  },
  [LEASE_ID]: {
    doc_id: LEASE_ID,
    kind: "Lease",
    title: "Gewerbemietvertrag",
    meta: `Commercial lease · ${LANDLORD} · 15 Jan 2021 · consolidated version with addendum no. 1 of 25 Jan 2024`,
    page: 1,
    lines: [
      LETTERHEAD,
      { id: "s2", text: "§2 Mietzeit: Das Mietverhältnis beginnt am 01.02.2021 und ist fest auf 10 Jahre bis zum 31.01.2031 abgeschlossen.", en: "The lease starts on 1 Feb 2021 and runs for a fixed term of 10 years until 31 Jan 2031.", muted: true },
      {
        id: "s4_1",
        text: "§4.1 Miete: Die Monatsmiete beträgt seit der letzten Anpassung zum 01.02.2024 3.800,00 €. Sie ist monatlich im Voraus bis zum 1. eines Monats zu zahlen.",
        en: "Since the last adjustment on 1 Feb 2024 the monthly rent is 3,800.00 €. It is payable monthly in advance by the 1st of each month.",
      },
      {
        id: "s4_2",
        text: "§4.2 Wertsicherung: Die Miete ist an den vom Statistischen Bundesamt ermittelten Verbraucherpreisindex für Deutschland (VPI, 2020 = 100) gebunden. Hat sich der Index gegenüber seinem Stand bei der letzten Anpassung (Januar 2024) um mindestens 5 % verändert, kann jede Partei durch schriftliche Erklärung eine Anpassung der Miete im gleichen prozentualen Verhältnis verlangen. Die geänderte Miete ist ab dem Monat zu zahlen, der auf den Zugang der Erklärung folgt. Eine Anpassung kann höchstens einmal innerhalb von 12 Monaten verlangt werden.",
        en: "The rent is linked to the consumer price index for Germany published by the Federal Statistical Office (VPI, 2020 = 100). If the index has changed by at least 5 % since its level at the last adjustment (January 2024), either party may request in writing an adjustment of the rent in the same proportion. The adjusted rent is payable from the month after the request is received. An adjustment may be requested at most once in 12 months.",
      },
      {
        id: "s4_3",
        text: "§4.3 Betriebskosten: Mit der Miete nach §4.1 sind sämtliche Betriebskosten mit Ausnahme von Strom und Gas abgegolten; Strom und Gas bezieht der Mieter direkt vom Versorger. Eine Erhöhung der Miete wegen gestiegener Betriebs- oder Bewirtschaftungskosten ist nur nach §4.2 möglich.",
        en: "The rent under §4.1 covers all operating costs except electricity and gas, which the tenant buys directly from the utility. The rent can be increased because of higher operating or management costs only under §4.2.",
      },
      {
        id: "addendum_1",
        text: "Nachtrag Nr. 1, Mietanpassung zum 01.02.2024: Die Parteien vereinbaren nach §4.2 eine Anpassung der Monatsmiete mit Wirkung zum 01.02.2024 auf 3.800,00 €. Maßgeblicher Indexstand für künftige Anpassungen ist der VPI für Januar 2024.",
        en: "Under §4.2 the parties agree to adjust the monthly rent to 3,800.00 € from 1 Feb 2024. The index level for future adjustments is the VPI for January 2024.",
        muted: true,
      },
    ],
  },
};

export function getDocument(id: string): SourceDoc {
  const d = DOCS[id];
  if (!d) throw new ApiError(404, `/documents/${id}`, `no document ${id}`, "NOT_FOUND");
  return clone(d);
}

// ───────────── The rent check (docs/PROOF.md §7.1) ─────────────

/** The backend's template letter, with the same computed amounts. German only, as on the backend. */
const LETTER: DraftMessage & { source: "TEMPLATE" } = {
  kind: "counter_proposal",
  to: LANDLORD,
  subject: "Ihre Mietanpassung ab 01.06.2025 – Bitte um Erläuterung gemäß §4.2 Mietvertrag",
  language: "de",
  source: "TEMPLATE",
  body: `Sehr geehrte Damen und Herren,

mit Ihrem Schreiben vom 30.04.2025 kündigen Sie an, die monatliche Miete ab dem 01.06.2025 von 3.800,00 € auf 4.104,00 € anzupassen.

Nach §4.2 des Mietvertrags ist eine Anpassung an den Verbraucherpreisindex (VPI) möglich, wenn sich der Index gegenüber seinem Stand bei der letzten Anpassung um mindestens 5 % verändert hat. Nach unseren Unterlagen lag der VPI im Januar 2024 bei 117,6 und im März 2025 bei 121,2; das entspricht einer Veränderung von 3,06 %. Eine Anpassung nach §4.2 ergibt sich daraus nach unserem Verständnis nicht; die Miete beträgt weiterhin 3.800,00 €.

Wir bitten Sie daher um eine kurze Erläuterung, auf welcher vertraglichen Grundlage die Änderung beruht, und um Ihre Berechnung. Sollten wir einen Umstand übersehen haben, freuen wir uns über Ihren Hinweis.

Mit freundlichen Grüßen

Vincenzo Amalfi
Pizzeria Amalfi`,
};

const actions = (): RecommendedAction[] => [
  {
    id: "act_rent_keep",
    kind: "keep_payment",
    title: `Keep paying ${eur(rentCheck.justified)} from 1 Jun 2025, not ${eur(BILLED)}`,
    detail: `Keep paying ${eur(rentCheck.justified)} for RENT, the amount the contract allows: ${eur(BILLED)} is billed, ${eur(rentCheck.disputedMonthly)} above it. Never withhold rent: pay the contractual amount and ask in writing.`,
    status: "pending",
    execution: "simulated",
  },
  {
    id: "act_rent_letter",
    kind: "send_letter",
    title: `Send a letter to ${LANDLORD}, in German`,
    detail: `Ask ${LANDLORD} in writing for the basis of the change: ${eur(rentCheck.disputedMonthly)} above what §4.2 allows.`,
    status: "pending",
    execution: "simulated",
    draft: LETTER,
  },
];

function baseFinding(): Finding {
  return {
    finding_id: RENT_FINDING,
    status: "ingested",
    classification: "unexplained",
    classification_history: [],
    vendor: LANDLORD,
    title: "Monthly rent from 1 Jun 2025",
    source_doc_id: UPLOAD_ID,
    line_item: {
      description: "Monthly rent",
      previous_value: RENT,
      current_value: BILLED,
      currency: "EUR",
      unit: "month",
      effective_from: EFFECTIVE,
      monthly_variance: BILLED - RENT,
      variance_pct: 8,
    },
    investigation_trace: [],
    reason: "",
    evidence_refs: [
      { type: "notice", doc_id: UPLOAD_ID, label: "Letter" },
      { type: "cost_memory", doc_id: null, label: "Cost Memory: Rent", ref: "RENT" },
    ],
    propagation: [],
    recommended_actions: [],
    quiet: false,
    acknowledged: false,
    created_at: nowIso(),
  };
}

let current: Finding | null = null;

function publish(patch: Partial<Finding>) {
  if (!current) return;
  current = { ...current, ...patch };
  findings = [...findings.filter((f) => f.finding_id !== current!.finding_id), current];
  mockBus.emit({ type: "finding.updated", data: clone(current) });
}

function classify(c: Classification, next: FindingStatus, reason: string) {
  if (!current) return;
  publish({ classification: c, classification_history: [...current.classification_history, c], status: next, reason });
}

function progress(label: string) {
  if (run) run = { ...run, steps: [...run.steps, label] };
}

async function step(s: Omit<TraceStep, "step" | "model" | "evidence_ref"> & { evidence_ref?: string }, ms: number) {
  if (!current) return;
  await delay(ms);
  const full: TraceStep = { evidence_ref: null, ...s, step: current.investigation_trace.length + 1, model: model(`${RENT_FINDING}-${s.call}`) };
  current = { ...current, investigation_trace: [...current.investigation_trace, full] };
  findings = [...findings.filter((f) => f.finding_id !== current!.finding_id), current];
  mockBus.emit({ type: "finding.step", data: { finding_id: RENT_FINDING, step: full } });
  progress(s.call);
  if (s.result) progress(s.result);
}

/** The rent adjustment arrives (upload, or the demo panel): read, check, investigate, classify, prepare. */
export async function runRentNotice(speed = 1) {
  const upload: KnownDocument = { doc_id: UPLOAD_ID, kind: "notice", label: "Rent adjustment, Main Street Real Estate", note: "€4,104 from 1 June", source: "upload", added_at: nowIso() };
  docs = [...docs.filter((d) => d.doc_id !== UPLOAD_ID), upload];
  mockBus.emit({ type: "document.added", data: upload });
  run = { document_id: UPLOAD_ID, run_id: RUN_ID, status: "RUNNING", steps: [`Received ${UPLOAD_ID}.pdf`], finding_ids: [RENT_FINDING], checked: true };
  status("costs", "New document from the landlord. Reading it…", true);

  current = baseFinding();
  publish({ status: "ingested" });
  await delay(800 / speed);
  publish({ status: "extracted" });
  progress("Read the values: rent €3,800.00 → €4,104.00 from 1 Jun 2025");
  await delay(800 / speed);
  classify("unexplained", "checked", `${eur(BILLED)} from 1 Jun 2025 against ${eur(RENT)} expected: +8.00 %`);
  progress("Needs evidence: the investigation looks for the reason next");
  await delay(800 / speed);
  publish({ status: "investigating" });
  status("costs", "Rent goes up 8%. Looking for the reason…", true);

  await step(
    {
      hypothesis: "A letter or notice about the change",
      tool: "search_inbox",
      call: `Searching the inbox for "rent increase" from ${LANDLORD}`,
      result: "Found 4 emails: the landlord's letter of 3 Apr 2025 is attached in the grease-trap maintenance thread. It names no index and no clause.",
      evidence_ref: LETTER_ID,
    },
    1500 / speed,
  );
  const letter: KnownDocument = { doc_id: LETTER_ID, kind: "notice", label: "Letter, Main Street Real Estate", note: "3 Apr 2025: +8 % for management costs", source: "inbox", added_at: nowIso() };
  docs = [...docs.filter((d) => d.doc_id !== LETTER_ID), letter];
  mockBus.emit({ type: "document.added", data: letter });
  await step(
    {
      hypothesis: "A contract clause that allows it",
      tool: "read_clause",
      call: `Reading §4.2 of the lease with ${LANDLORD}`,
      result: "Index clause: an adjustment only if the VPI has moved at least 5 % since January 2024, in writing, at most once in 12 months. §4.3: higher operating costs raise the rent only under §4.2.",
      evidence_ref: `${LEASE_ID}#s4_2`,
    },
    1500 / speed,
  );
  await step(
    {
      hypothesis: "The reference index then and now",
      tool: "get_reference_index",
      call: "Looking up the consumer price index for Jan 2024 and Mar 2025",
      result: "117.6 in Jan 2024; 121.2 in Mar 2025 (demo values)",
      evidence_ref: "ref_vpi_demo",
    },
    1300 / speed,
  );
  await step(
    {
      hypothesis: "The reference index then and now",
      tool: "compute",
      call: "Computing what the index clause allows",
      result: `Index moved ${(rentCheck.changeBps / 100).toFixed(2)}%, less than 5.00%: the amount stays at ${eur(rentCheck.justified)}`,
    },
    1200 / speed,
  );
  await delay(500 / speed);

  const inWindow = rentCheck.disputedMonthly * PAYMENTS.length;
  publish({
    evidence_refs: [
      { type: "notice", doc_id: UPLOAD_ID, label: "Letter" },
      { type: "notice", doc_id: LETTER_ID, label: "Letter", ref: LETTER_ID },
      { type: "lease", doc_id: LEASE_ID, item_id: "s4_2", label: "Lease §4.2", ref: `${LEASE_ID}#s4_2` },
      { type: "cost_memory", doc_id: null, label: "Cost Memory: Rent", ref: "RENT" },
      { type: "email", doc_id: null, label: "Email", ref: "mail_main_street_2025_04_03" },
      { type: "reference", doc_id: null, label: "Reference values (demo)", ref: "ref_vpi_demo" },
    ],
    verdict: indexRentVerdict({
      clause: "§4.2",
      baseIndex: VPI_BASE,
      currentIndex: VPI_NOW,
      thresholdBps: THRESHOLD_BPS,
      changeBps: rentCheck.changeBps,
      allowed: rentCheck.allowed,
      current: RENT,
      billed: BILLED,
      justified: rentCheck.justified,
      disputed: rentCheck.disputedMonthly,
      disputedAnnual: rentCheck.disputedAnnual,
      disputedInWindow: inWindow,
      payments: PAYMENTS,
      effectiveFrom: EFFECTIVE,
      sourceDoc: UPLOAD_ID,
    }),
    financial_impact: {
      justified_value: rentCheck.justified,
      disputed_amount_monthly: rentCheck.disputedMonthly,
      disputed_amount_annual: rentCheck.disputedAnnual,
      disputed_in_window: inWindow,
    },
    propagation: paymentPropagation({
      decision: "KEEP",
      label: "Rent",
      current: RENT,
      proposed: rentCheck.justified,
      billed: BILLED,
      effectiveFrom: EFFECTIVE,
      payments: PAYMENTS,
      outflowDifference: 0,
    }),
    recommended_actions: actions(),
  });
  classify("conflict", "classified", `Conflict: ${eur(BILLED)} billed, ${eur(rentCheck.justified)} allowed`);
  progress(`Keep paying ${eur(RENT)} for RENT; ${eur(inWindow)} disputed in the next 13 weeks`);
  await delay(400 / speed);
  publish({ status: "pending_approval", reason: `Conflict: ${eur(BILLED)} billed, ${eur(rentCheck.justified)} allowed` });
  const headline = `${LANDLORD}: rent ${eur(RENT)} → ${eur(BILLED)} from 1 Jun 2025: ${eur(rentCheck.disputedMonthly)} more than allowed`;
  run = { ...run!, status: "SUCCEEDED", headline, steps: [...run!.steps, headline] };
  status("costs", "The rent increase conflicts with your lease. Actions prepared.", false);
  await log("agent", `Rent ${eur(RENT)} → ${eur(BILLED)}: conflicts with lease §4.2, allowed ${eur(rentCheck.justified)}, disputed ${eur(rentCheck.disputedAnnual)} a year`, {
    agent: "costs",
    autonomy: "approval_required",
    execution: "simulated",
    model: model(`${RENT_FINDING}-classified`),
  });
}

/** POST /documents in mock mode: only the demo rent adjustment is checked, like the demo backend. */
export async function uploadDocument(file: File): Promise<DocumentRun> {
  await delay(300);
  if (file.size > 5 * 1024 * 1024) throw new ApiError(413, "/documents", "above 5 MB", "FILE_TOO_LARGE");
  if (!/rent_adjustment/i.test(file.name)) {
    if (/lease|miet|ctr_|contract|vertrag/i.test(file.name)) throw new ApiError(409, "/documents", "already processed", "DOCUMENT_ALREADY_PROCESSED");
    throw new Error("In mock mode Vero can only check the demo rent adjustment. Start the app in live mode, with the AI service, to check your own bills.");
  }
  if (run?.status === "RUNNING") throw new ApiError(409, "/documents", "a run is in progress", "RUN_IN_PROGRESS");
  if (findings.some((f) => f.finding_id === RENT_FINDING)) throw new ApiError(409, "/documents", "already processed", "DOCUMENT_ALREADY_PROCESSED");
  void runRentNotice();
  return { document_id: UPLOAD_ID, run_id: RUN_ID, status: "RUNNING", steps: [], finding_ids: [], checked: false };
}

export async function getDocumentRun(id: string): Promise<DocumentRun> {
  await delay(80);
  if (!run || run.run_id !== id) throw new ApiError(404, `/runs/${id}`, `no run ${id}`, "NOT_FOUND");
  return clone(run);
}

/** Approve some prepared actions, decline the rest; the finding is resolved once all are decided. */
export async function decideFinding(id: string, approve: string[], reject: string[]): Promise<void> {
  await delay(400);
  if (!current || current.finding_id !== id) throw new ApiError(404, `/findings/${id}`, `no finding ${id}`, "NOT_FOUND");
  const next = current.recommended_actions.map((a) =>
    approve.includes(a.id) ? { ...a, status: "approved" as const } : reject.includes(a.id) ? { ...a, status: "rejected" as const } : a,
  );
  const done = next.every((a) => a.status !== "pending");
  publish({ recommended_actions: next, ...(done ? { status: "resolved" as const, resolved_at: nowIso() } : {}) });
  for (const a of next.filter((x) => approve.includes(x.id))) {
    await log("owner", `Approved (simulated): ${a.title}`, { agent: "costs", autonomy: "approval_required", execution: "simulated" });
  }
  for (const a of next.filter((x) => reject.includes(x.id))) {
    await log("owner", `Declined: ${a.title}`, { agent: "costs", autonomy: "approval_required" });
  }
}

resetCosts();
