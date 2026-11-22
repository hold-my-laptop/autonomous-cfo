/**
 * Vero, the cost check, in mock mode: the reference demo of docs/PROOF.md §7, so mock and live tell the
 * same story with the same numbers. The texts of the lease, the landlord's letter and the rent adjustment
 * are the dataset's (services/api fixtures/proof/dataset.json). Every number comes from engine.ts
 * (the backend's formulas) or the bank statement.
 *
 * Story
 *  - Preloaded (reset): three changes handled quietly (Fattoria and Asimex price lists, the Asimex
 *    surcharge found by a scripted investigation) and three routine checks (rent, utilities, CHF tranche).
 *  - The upload: the landlord's rent adjustment (doc_rent_adjustment_2025_06.pdf) asks for 4.104,00 €
 *    instead of 3.800,00 € from 1 June. Vero finds the landlord's letter in the grease-trap email thread,
 *    reads lease §4.2 (adjustment only if the VPI moved at least 5 %), looks up the VPI (117.6 → 121.2,
 *    3.06 %) and computes: no adjustment allowed, the rent stays 3.800,00 €. Conflicts with contract:
 *    304 € a month disputed, 3.648 € a year, 912 € inside the 13-week window. Actions: keep paying
 *    3.800,00 €, send a letter (German).
 *
 * The mock reads no PDF: every upload is treated as the demo rent adjustment.
 */
import type {
  ActionDecisions,
  CostMemoryRow,
  Digest,
  DigestChange,
  EvidenceRef,
  Finding,
  FindingStatus,
  KnownDocument,
  RecommendedAction,
  RoutineCheck,
  SourceDoc,
  TraceStep,
  UploadRun,
} from "../types";
import { ApiError } from "../client";
import { mockBus } from "../mockBus";
import { annualize, paymentsInWindow, surchargeRule, variance, vpiThresholdIndexation } from "../../features/vero/engine";
import { clone, delay, log, model, nowIso, status } from "./runtime";
import { type Category, monthlyTotals, TXNS } from "./statement";

const AS_OF = "2025-04-14"; // the mock's demo clock (Cost Memory is built from what was seen until then)
/** The 13-week window of PROOF §7.1 (5 May – 3 Aug 2025): the backend's demo date. */
const WINDOW_FROM = "2025-05-05";
const WINDOW_DAYS = 91;

const de = (v: number) => `${v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
const deDate = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;

// ───────────── The hero's inputs (PROOF §4, §7.1) ─────────────

export const LANDLORD = "Main Street Real Estate GmbH";
const RENT = 3800; // lease §4.1, and what the statement shows every month
const BILLED = 4104; // the rent adjustment, from 1 June
const EFFECTIVE = "2025-06-01";
const VPI = { base: 1176, current: 1212, basePeriod: "2024-01", currentPeriod: "2025-03" }; // tenths, demo values
const THRESHOLD_BPS = 500; // lease §4.2

const rentVariance = variance(RENT, BILLED);
const vpi = vpiThresholdIndexation(RENT, VPI.base, VPI.current, THRESHOLD_BPS);
const disputedMonthly = Math.max(0, BILLED - vpi.justified);
const windowPayments = paymentsInWindow(EFFECTIVE, 1, WINDOW_FROM, WINDOW_DAYS);
const disputedInWindow = disputedMonthly * windowPayments.length;

const LEASE_ID = "lease_main_street_2021";
const NOTICE_ID = "ntc_main_street_2025_04";
const NOTICE_EMAIL = "mail_main_street_2025_04_03";
const UPLOAD_ID = "doc_rent_adjustment_2025_06";
export const DEMO_UPLOAD_NAME = `${UPLOAD_ID}.pdf`;
const HERO_ID = "fnd_rent_2025_06";
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

// ───────────── Documents Vero knows (as GET /documents lists them) ─────────────

const doc = (doc_id: string, kind: KnownDocument["kind"], label: string, note: string, source: KnownDocument["source"], date: string): KnownDocument => ({
  doc_id,
  kind,
  label,
  note,
  source,
  added_at: `${date}T00:00:00Z`,
});

const PRELOADED_DOCS: KnownDocument[] = [
  doc(LEASE_ID, "lease", "Commercial lease", LANDLORD, "history", "2021-01-15"),
  doc("ctr_city_energy_2025", "contract", "Payment plan 2025", "City Energy & Utilities AG", "history", "2024-12-12"),
  doc("ctr_luigi_2024", "contract", "Silent partnership agreement", "Luigi Amalfi (Luigi's Ristorante Zurich)", "history", "2024-12-10"),
  doc("pl_fattoria_2025", "contract", "Price list 2025", "Fattoria La Vino GmbH", "history", "2025-03-18"),
  doc("pl_asimex_2025_q2", "contract", "Price list Q2 2025", "Asimex Wholesale Ltd", "history", "2025-03-20"),
  doc("terms_asimex_2025", "contract", "General terms of delivery and payment, valid from 1 May 2025", "Asimex Wholesale Ltd", "history", "2025-04-14"),
  doc("inv_FLV-16261", "invoice", "Invoice", "Fattoria La Vino GmbH · FLV-16261", "history", "2025-03-07"),
  doc("inv_FLV-94818", "invoice", "Invoice", "Fattoria La Vino GmbH · FLV-94818", "history", "2025-04-28"),
  doc("inv_AS-80497", "invoice", "Invoice", "Asimex Wholesale Ltd · AS-80497", "history", "2025-03-08"),
  doc("inv_AS-72217", "invoice", "Invoice", "Asimex Wholesale Ltd · AS-72217", "history", "2025-04-26"),
  doc("inv_AS-11607", "invoice", "Invoice", "Asimex Wholesale Ltd · AS-11607", "history", "2025-05-03"),
  doc(NOTICE_ID, "notice", "Notice about the lease", LANDLORD, "inbox", "2025-04-03"),
];
const UPLOAD_DOC = doc(UPLOAD_ID, "notice", "Rent adjustment", LANDLORD, "upload", "2025-04-30");

const LETTERHEAD = { text: `${LANDLORD} · Hausverwaltung · Mainufer 18 · 60311 Frankfurt am Main`, muted: true };
const RECIPIENT = { text: "Pizzeria Amalfi · Herrn Vincenzo Amalfi · Hauptstraße 12 · 60311 Frankfurt am Main", muted: true };

const DOCS: Record<string, SourceDoc> = {
  [LEASE_ID]: {
    doc_id: LEASE_ID,
    kind: "Lease",
    title: "Gewerbemietvertrag",
    meta: `Commercial lease · ${LANDLORD} · 15 Jan 2021 · consolidated version with addendum no. 1 of 25 Jan 2024`,
    page: 1,
    lines: [
      LETTERHEAD,
      {
        text: "§4.1 Miete: Die Monatsmiete beträgt seit der letzten Anpassung zum 01.02.2024 3.800,00 €. Sie ist monatlich im Voraus bis zum 1. eines Monats zu zahlen.",
        en: "Since the last adjustment on 1 Feb 2024 the monthly rent is 3,800.00 €, payable in advance by the 1st of each month.",
        box: { label: "§4.1 rent", tone: "basis" },
      },
      {
        text: "§4.2 Wertsicherung: Die Miete ist an den vom Statistischen Bundesamt ermittelten Verbraucherpreisindex für Deutschland (VPI, 2020 = 100) gebunden. Hat sich der Index gegenüber seinem Stand bei der letzten Anpassung (Januar 2024) um mindestens 5 % verändert, kann jede Partei durch schriftliche Erklärung eine Anpassung der Miete im gleichen prozentualen Verhältnis verlangen.",
        en: "The rent is linked to the German consumer price index (VPI, 2020 = 100). If the index has changed by at least 5 % since the last adjustment (January 2024), either party may request in writing an adjustment in the same proportion.",
        box: { label: "§4.2 basis", tone: "basis" },
      },
      {
        text: "§4.3 Betriebskosten: Eine Erhöhung der Miete wegen gestiegener Betriebs- oder Bewirtschaftungskosten ist nur nach §4.2 möglich.",
        en: "The rent can be increased because of higher operating or management costs only under §4.2.",
      },
      { text: "Frankfurt am Main, 15.01.2021 · Main Street Real Estate GmbH · Vincenzo Amalfi", muted: true },
    ],
  },
  [NOTICE_ID]: {
    doc_id: NOTICE_ID,
    kind: "Notice",
    title: "Mitteilung zum Mietverhältnis",
    meta: `Notice about the lease · ${LANDLORD} · 3 Apr 2025 · attached to an email about the courtyard grease trap`,
    page: 1,
    lines: [
      LETTERHEAD,
      RECIPIENT,
      { text: "Frankfurt am Main, 03.04.2025", muted: true },
      {
        text: "Erhöhung der Miete um 8 % ab 01.06.2025 aufgrund gestiegener Bewirtschaftungskosten.",
        en: "Increase of the rent by 8 % from 1 Jun 2025 due to higher management costs. No index and no lease clause cited.",
        box: { label: "Reason given", tone: "neutral" },
      },
      { text: "Die neue Monatsmiete teilen wir Ihnen mit gesondertem Schreiben mit.", en: "We will tell you the new monthly rent in a separate letter.", muted: true },
    ],
  },
  [UPLOAD_ID]: {
    doc_id: UPLOAD_ID,
    kind: "Notice",
    title: "Mietanpassung",
    meta: `Rent adjustment · ${LANDLORD} · 30 Apr 2025 · uploaded`,
    page: 1,
    lines: [
      LETTERHEAD,
      RECIPIENT,
      { text: "Frankfurt am Main, 30.04.2025", muted: true },
      { text: "Sehr geehrter Herr Amalfi, wie angekündigt passen wir Ihre Miete wie folgt an:", en: "As announced we adjust your rent as follows:" },
      { text: `Bisherige Miete ${de(RENT)}`, en: "Rent so far", box: { label: "Basis", tone: "basis" } },
      { text: `Neue Miete ab ${deDate(EFFECTIVE)}: ${de(BILLED)}`, en: "New rent", strong: true, box: { label: "Billed", tone: "billed" } },
      { text: "Bitte passen Sie Ihren Dauerauftrag entsprechend an.", en: "Please change your standing order accordingly.", muted: true },
    ],
  },
};

// ───────────── State ─────────────

let docs: KnownDocument[] = [];
let findings: Finding[] = [];
/** Why each finding has its label, in one line (GET /digest's `reason`). */
let reasons: Record<string, string> = {};
let running = false;

export function resetCosts() {
  docs = clone(PRELOADED_DOCS);
  findings = preloadedFindings();
  reasons = {
    fnd_flv_94818: "Every difference matches the Price list 2025",
    fnd_as_72217: "Every difference matches the Price list Q2 2025",
    fnd_as_11607: `The surcharge of ${de(24.5)} applies: announced on 14 Apr, goods ${de(558.71)} under ${de(600)}`,
  };
  running = false;
}

// ───────────── Cost Memory (from the statement) ─────────────

// Same shape as the real GET /cost-memory: fixed costs expect last full month's amount (March),
// suppliers are checked per invoice. The CHF transfer is 4,166.67 CHF a month.
const LAST_FULL_MONTH = "2025-03";
const CHF_MONTHLY = 4166.67;
const MEMORY: { ref: string; label: string; vendor: string; category: Category; kind: "fixed" | "supplier"; sources: CostMemoryRow["sources"] }[] = [
  { ref: "RENT", label: "Rent", vendor: LANDLORD, category: "rent", kind: "fixed", sources: ["statement", "contract"] },
  { ref: "UTIL", label: "Energy & utilities", vendor: "City Energy & Utilities AG", category: "utilities", kind: "fixed", sources: ["statement", "contract"] },
  { ref: "XFER", label: "CHF transfer", vendor: "Luigi Amalfi, Zurich", category: "fx_transfer", kind: "fixed", sources: ["statement", "contract"] },
  { ref: "SOC", label: "Social security", vendor: "Techniker Krankenkasse", category: "social_security", kind: "fixed", sources: ["statement"] },
  { ref: "ASIMEX", label: "Supplier", vendor: "Asimex Wholesale Ltd", category: "food", kind: "supplier", sources: ["price_list", "invoice"] },
  { ref: "FATTORIA", label: "Supplier", vendor: "Fattoria La Vino GmbH", category: "wine", kind: "supplier", sources: ["price_list", "invoice"] },
];

export function getMemory(): CostMemoryRow[] {
  return MEMORY.map((m) => {
    const march = -(monthlyTotals(AS_OF, m.category).get(LAST_FULL_MONTH) ?? 0);
    const chf = m.category === "fx_transfer";
    return {
      cost_ref: m.ref,
      label: m.label,
      vendor: m.vendor,
      kind: m.kind,
      expected:
        m.kind === "fixed"
          ? chf
            ? { amount: CHF_MONTHLY, currency: "CHF", eur_amount: march }
            : { amount: march, currency: "EUR" }
          : undefined,
      prices: 0,
      sources: m.sources,
    };
  });
}

// ───────────── Preloaded findings (PROOF §7.2), handled quietly ─────────────

function quietInvoice(id: string, vendor: string, number: string, billed: number, title: string, period: string, evidence: EvidenceRef[], extra: Partial<Finding> = {}): Finding {
  return {
    finding_id: id,
    title,
    status: "classified",
    classification: "verified",
    classification_history: ["verified"],
    vendor,
    cost_ref: vendor.startsWith("Asimex") ? "ASIMEX" : "FATTORIA",
    source_doc_id: `inv_${number}`,
    run_id: null,
    quiet: true,
    in_digest: true,
    line_item: {
      description: `Invoice ${number}`,
      previous_value: null,
      current_value: billed,
      currency: "EUR",
      unit: "invoice",
      effective_from: period,
      monthly_variance: 0,
      variance_pct: null,
    },
    differences: [{ description: "Unit prices changed since the previous invoice", explained: true }],
    investigation_trace: [],
    evidence_refs: [{ kind: "document", doc_id: `inv_${number}`, label: `Invoice ${number}` }, ...evidence],
    recommended_actions: [],
    created_at: "2025-05-05T08:00:00Z",
    ...extra,
  };
}

function preloadedFindings(): Finding[] {
  const surcharge = surchargeRule(558.71, "2025-05-03", 24.5, 600, "2025-05-01");
  return [
    quietInvoice("fnd_flv_94818", "Fattoria La Vino GmbH", "FLV-94818", 897.08, "Fattoria La Vino GmbH: new prices on FLV-94818", "2025-04-28", [
      { kind: "price_list_item", doc_id: "pl_fattoria_2025", label: "Price list 2025" },
    ]),
    quietInvoice("fnd_as_72217", "Asimex Wholesale Ltd", "AS-72217", 687.23, "Asimex Wholesale Ltd: new prices on AS-72217", "2025-04-26", [
      { kind: "price_list_item", doc_id: "pl_asimex_2025_q2", label: "Price list Q2 2025" },
    ]),
    quietInvoice(
      "fnd_as_11607",
      "Asimex Wholesale Ltd",
      "AS-11607",
      624.03,
      `Asimex Wholesale Ltd: Logistikzuschlag (${de(24.5)}) on AS-11607`,
      "2025-05-03",
      [
        { kind: "email", ref: "mail_asimex_2025_04_14", label: "Email of 14 Apr 2025" },
        { kind: "clause", doc_id: "terms_asimex_2025", label: "Terms of delivery" },
      ],
      {
        classification_history: ["unexplained", "verified"],
        differences: [{ description: "Logistikzuschlag", explained: true }],
        investigation: {
          mode: "fallback",
          formula: { formula_id: "surcharge_rule", net_goods: 558.71, below_net_goods: 600, surcharge: 24.5, applies: surcharge.applies },
          billed: 24.5,
          justified: surcharge.surcharge,
          searched: [
            "Searching your inbox for Logistikzuschlag from Asimex Wholesale Ltd",
            "Reading the terms of delivery with Asimex Wholesale Ltd",
            `The surcharge of ${de(24.5)} applies`,
          ],
          problems: [],
        },
      },
    ),
  ];
}

// ───────────── Routine checks (PROOF §7.3): April payments against their contract ─────────────

function routineChecks(): RoutineCheck[] {
  const april = (c: Category) => TXNS.find((t) => t.category === c && t.date.startsWith("2025-04"));
  const rent = april("rent");
  const util = april("utilities");
  const chf = april("fx_transfer");
  const rows: RoutineCheck[] = [];
  if (rent) rows.push({ cost_ref: "RENT", counterparty: LANDLORD, date: rent.date, paid: -rent.amountEur, expected: RENT, currency: "EUR", doc_id: LEASE_ID });
  if (util) rows.push({ cost_ref: "UTIL", counterparty: "City Energy & Utilities AG", date: util.date, paid: -util.amountEur, expected: 650, currency: "EUR", doc_id: "ctr_city_energy_2025" });
  if (chf)
    rows.push({
      cost_ref: "XFER",
      counterparty: "Luigi Amalfi (Luigi's Ristorante Zurich)",
      date: chf.date,
      paid: -chf.amountOriginal,
      expected: CHF_MONTHLY,
      currency: "CHF",
      eur_amount: -chf.amountEur,
      doc_id: "ctr_luigi_2024",
    });
  return rows;
}

// ───────────── Reads ─────────────

export function getDocument(id: string): SourceDoc {
  const d = DOCS[id];
  if (!d) throw new Error(`Document ${id} not found`);
  return clone(d);
}

export const getDocuments = () => clone(docs);
export const getFindings = () => clone(findings);

const byPeriod = (f: Finding) => f.line_item.effective_from ?? f.created_at;

export function getDigest(): Digest {
  const open = findings.filter((f) => f.in_digest).sort((a, b) => byPeriod(a).localeCompare(byPeriod(b)));
  const changes: DigestChange[] = open.map((f) => ({
    finding_id: f.finding_id,
    title: f.title,
    classification: f.classification,
    quiet: f.quiet,
    investigation_mode: f.investigation?.mode,
    reason: reasons[f.finding_id] ?? "",
    actions: f.recommended_actions.filter((a) => a.status === "pending").map((a) => ({ id: a.id, type: a.type, text: a.title })),
  }));
  const checks = routineChecks();
  return {
    as_of: WINDOW_FROM,
    counts: {
      changes: changes.length,
      handled_quietly: changes.filter((c) => c.quiet).length,
      needs_owner: changes.filter((c) => !c.quiet).length,
      routine_checks: checks.length,
    },
    changes,
    routine_checks: checks,
  };
}

// ───────────── The upload: the rent adjustment (PROOF §7.1) ─────────────

const LETTER_BODY = `Sehr geehrte Damen und Herren,

mit Ihrem Schreiben vom 30.04.2025 teilen Sie uns eine Monatsmiete von ${de(BILLED)} ab dem ${deDate(EFFECTIVE)} mit; bisher beträgt sie ${de(RENT)}.

Nach §4.2 unseres Mietvertrags kann die Miete nur angepasst werden, wenn sich der Verbraucherpreisindex (VPI) seit der letzten Anpassung (Januar 2024) um mindestens 5 % verändert hat. Der VPI ist von ${(VPI.base / 10).toLocaleString("de-DE")} auf ${(VPI.current / 10).toLocaleString("de-DE")} (März 2025) gestiegen, also um ${(vpi.changeBps / 100).toLocaleString("de-DE")} %. Eine Anpassung ist danach nach unserem Verständnis nicht möglich; die Miete beträgt weiterhin ${de(vpi.justified)}.

Wir bitten Sie daher um eine kurze Erläuterung, auf welcher vertraglichen Grundlage die Änderung beruht, und um Ihre Berechnung. Sollten wir einen Umstand übersehen haben, freuen wir uns über Ihren Hinweis.

Mit freundlichen Grüßen

Vincenzo Amalfi
Pizzeria Amalfi`;

const heroActions = (): RecommendedAction[] => [
  {
    id: "rec_keep_rent",
    type: "keep_payment",
    title: `Keep paying ${de(RENT)} instead of ${de(BILLED)} from ${deDate(EFFECTIVE)}`,
    detail: `The lease allows no adjustment yet, so the rent stays ${de(vpi.justified)}. Paying it keeps you out of arrears while the landlord explains.`,
    lane: "needs_approval",
    status: "pending",
  },
  {
    id: "rec_letter_rent",
    type: "send_letter",
    title: `Send a letter to ${LANDLORD}, in German`,
    detail: "Neutral, cites §4.2 of the lease and the index change, and asks for the basis of the increase.",
    lane: "needs_approval",
    status: "pending",
    letter: {
      to: LANDLORD,
      subject: `Ihre Mietanpassung ab ${deDate(EFFECTIVE)} – Bitte um Erläuterung gemäß §4.2 Mietvertrag`,
      body: LETTER_BODY,
      language: "de",
      source: "template",
    },
  },
];

function heroFinding(): Finding {
  return {
    finding_id: HERO_ID,
    title: `${LANDLORD}: rent ${de(RENT)} → ${de(BILLED)} from ${deDate(EFFECTIVE)}`,
    status: "ingested",
    classification: "unexplained",
    classification_history: [],
    vendor: LANDLORD,
    cost_ref: "RENT",
    source_doc_id: UPLOAD_ID,
    run_id: "run_rent_upload",
    quiet: false,
    in_digest: true,
    line_item: {
      description: "Monthly rent from 1 Jun",
      previous_value: RENT,
      current_value: BILLED,
      currency: "EUR",
      unit: "month",
      effective_from: EFFECTIVE,
      monthly_variance: rentVariance.monthly,
      variance_pct: rentVariance.pct,
    },
    differences: [{ description: "Monthly rent from 1 Jun", explained: false }],
    investigation_trace: [],
    evidence_refs: [
      { kind: "document", doc_id: UPLOAD_ID, label: "Rent adjustment" },
      { kind: "cost_item", label: "What you usually pay (RENT)" },
    ],
    recommended_actions: [],
    created_at: nowIso(),
  };
}

function upsert(f: Finding) {
  findings = [...findings.filter((x) => x.finding_id !== f.finding_id), f];
  mockBus.emit({ type: "finding.updated", data: clone(f) });
}

function patch(id: string, p: Partial<Finding>) {
  const f = findings.find((x) => x.finding_id === id);
  if (f) upsert({ ...f, ...p });
}

const hero = () => findings.find((f) => f.finding_id === HERO_ID)!;

/** The visible investigation, step by step (the same labels the backend's tools write). */
const HERO_STEPS: (Omit<TraceStep, "step"> & { cite?: EvidenceRef[] })[] = [
  {
    hypothesis: "H1 · A letter or notice about the change",
    tool: "search_inbox",
    label: `Searching your inbox for rent increase from ${LANDLORD}`,
    result: "Found 1 email: the landlord's letter, attached in the thread about the courtyard grease trap",
    cite: [
      { kind: "email", ref: NOTICE_EMAIL, label: "Email of 3 Apr 2025" },
      { kind: "document", doc_id: NOTICE_ID, label: "Landlord's letter" },
    ],
  },
  {
    hypothesis: "H2 · A contract clause that allows it",
    tool: "read_clause",
    label: `Reading §4.2 of the lease with ${LANDLORD}`,
    result: "Index clause: an adjustment needs a VPI change of at least 5 % since January 2024",
    cite: [{ kind: "clause", doc_id: LEASE_ID, item_id: "s4_2", ref: `${LEASE_ID}#s4_2`, label: "Lease §4.2" }],
  },
  {
    hypothesis: "H3 · The reference index then and now",
    tool: "get_reference_index",
    label: "Looking up the consumer price index for Jan 2024 and Mar 2025",
    result: `${VPI.base / 10} in Jan 2024, ${VPI.current / 10} in Mar 2025 (demo values)`,
    cite: [{ kind: "reference", ref: "ref_vpi_demo", label: "Consumer price index (demo values)" }],
  },
  {
    hypothesis: "H3 · The reference index then and now",
    tool: "compute",
    label: `Index moved ${(vpi.changeBps / 100).toFixed(2)}%, less than ${(THRESHOLD_BPS / 100).toFixed(2)}%: the amount stays at ${de(vpi.justified)}`,
  },
];

async function runHero(onProgress: (latest: string) => void, speed = 1) {
  const say = (msg: string, busy = true) => {
    status("costs", msg, busy);
    onProgress(msg);
  };
  docs = [...docs.filter((d) => d.doc_id !== UPLOAD_ID), UPLOAD_DOC];
  mockBus.emit({ type: "document.added", data: clone(UPLOAD_DOC) });

  say("Reading the rent adjustment…");
  upsert(heroFinding());
  await delay(900 / speed);
  patch(HERO_ID, { status: "extracted" });
  await delay(800 / speed);
  patch(HERO_ID, { status: "checked", classification: "unexplained", classification_history: ["unexplained"] });
  reasons[HERO_ID] = `${de(BILLED)} from 1 Jun against ${de(RENT)} expected: +${rentVariance.pct.toLocaleString("de-DE")} %`;
  say(`${de(BILLED)} instead of ${de(RENT)} from 1 June: looking for the reason…`);
  await delay(800 / speed);
  patch(HERO_ID, { status: "investigating" });

  for (const s of HERO_STEPS) {
    await delay(1300 / speed);
    const { cite, ...step } = s;
    const f = hero();
    const full: TraceStep = { ...step, step: f.investigation_trace.length + 1 };
    patch(HERO_ID, {
      investigation_trace: [...f.investigation_trace, full],
      evidence_refs: [...f.evidence_refs, ...(cite ?? [])],
    });
    mockBus.emit({ type: "finding.step", data: { finding_id: HERO_ID, step: full } });
    say(s.label);
  }

  await delay(600 / speed);
  reasons[HERO_ID] = `Recomputed: ${de(vpi.justified)} allowed, ${de(BILLED)} billed`;
  patch(HERO_ID, {
    status: "classified",
    classification: "conflict",
    classification_history: ["unexplained", "conflict"],
    investigation: {
      mode: "fallback",
      formula: {
        formula_id: "vpi_threshold_indexation",
        base_amount: RENT,
        base_index: VPI.base / 10,
        current_index: VPI.current / 10,
        threshold_pct: THRESHOLD_BPS / 100,
        change_pct: vpi.changeBps / 100,
        adjustment_allowed: vpi.allowed,
        justified: vpi.justified,
      },
      billed: BILLED,
      justified: vpi.justified,
      disputed: disputedMonthly,
      searched: HERO_STEPS.map((s) => s.label),
      problems: [],
    },
    financial_impact: {
      billed: BILLED,
      justified: vpi.justified,
      disputed_monthly: disputedMonthly,
      disputed_annual: annualize(disputedMonthly),
      disputed_in_window: disputedInWindow,
    },
    payment: {
      decision: "keep",
      cost_ref: "RENT",
      effective_from: EFFECTIVE,
      current_amount: RENT,
      billed_amount: BILLED,
      // Keep: the rent stays what the forecast already has, so the forecast doesn't change.
      proposed_amount: vpi.justified,
    },
  });
  await delay(400 / speed);
  patch(HERO_ID, { status: "pending_approval", recommended_actions: heroActions() });
  say("Rent increase conflicts with your lease. Actions prepared.", false);
  await log("agent", `Rent ${de(RENT)} → ${de(BILLED)}: conflicts with lease §4.2, allowed ${de(vpi.justified)}, ${de(annualize(disputedMonthly))} a year disputed`, {
    agent: "costs",
    autonomy: "approval_required",
    execution: "simulated",
    model: model(`${HERO_ID}-classified`),
  });
}

/**
 * Upload a bill or notice: the same rules as POST /documents (a PDF of at most 5 MB, one run at a time,
 * a document only once). The mock treats every upload as the demo rent adjustment.
 */
export async function uploadBill(file: File, onProgress?: (run: UploadRun) => void, speed = 1): Promise<UploadRun> {
  const path = "/documents";
  if (file.size > MAX_UPLOAD_BYTES) throw new ApiError(413, path, "The file is larger than 5 MB.", "FILE_TOO_LARGE");
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") throw new ApiError(422, path, "Only PDF files can be checked.", "NOT_A_PDF");
  if (running) throw new ApiError(409, path, "A check is already running.", "RUN_IN_PROGRESS");
  if (findings.some((f) => f.finding_id === HERO_ID)) throw new ApiError(409, path, "This document was already processed.", "DOCUMENT_ALREADY_PROCESSED");
  running = true;
  const run = (p: Partial<UploadRun>): UploadRun => ({ run_id: "run_rent_upload", status: "running", finding_ids: [HERO_ID], ...p });
  try {
    await log("owner", `Uploaded ${file.name}`, { agent: "costs" });
    await runHero((latest) => onProgress?.(run({ latest })), speed);
    return run({ status: "succeeded", headline: `Rent conflicts with lease §4.2: keep paying ${de(RENT)}, ${de(disputedMonthly)} a month disputed.` });
  } finally {
    running = false;
  }
}

/** Demo button: the rent adjustment arrives as if uploaded. */
export const runRentUpload = () => uploadBill(new File(["demo"], DEMO_UPLOAD_NAME, { type: "application/pdf" }));

/** Approve or reject each prepared action; the finding is resolved once every action is decided. */
export async function decideActions(decisions: ActionDecisions): Promise<void> {
  await delay(400);
  for (const [id, verdict] of Object.entries(decisions)) {
    const f = findings.find((x) => x.recommended_actions.some((a) => a.id === id));
    const a = f?.recommended_actions.find((x) => x.id === id);
    if (!f || !a) throw new ApiError(404, `/recommendations/${id}/${verdict}`, `Action ${id} not found`, "NOT_FOUND");
    if (a.status !== "pending") throw new ApiError(409, `/recommendations/${id}/${verdict}`, "Already decided", "NOT_PENDING");
    const actions = f.recommended_actions.map((x) => (x.id === id ? { ...x, status: verdict === "approve" ? ("approved" as const) : ("rejected" as const) } : x));
    const done = actions.every((x) => x.status !== "pending");
    const next: Partial<Finding> = { recommended_actions: actions };
    if (done) Object.assign(next, { status: "resolved" as FindingStatus, in_digest: false, resolved_at: nowIso() });
    patch(f.finding_id, next);
    await log("owner", `${verdict === "approve" ? "Approved" : "Rejected"}: ${a.title}${verdict === "approve" ? " (simulated)" : ""}`, {
      agent: "costs",
      autonomy: "approval_required",
      execution: "simulated",
      decisionId: id,
    });
  }
}

/** A quiet finding leaves the digest with one tap (POST /findings/{id}/acknowledge). */
export async function acknowledgeFinding(id: string): Promise<void> {
  await delay(250);
  const f = findings.find((x) => x.finding_id === id);
  if (!f) throw new ApiError(404, `/findings/${id}/acknowledge`, `Finding ${id} not found`, "NOT_FOUND");
  if (!f.quiet || !f.in_digest) throw new ApiError(409, `/findings/${id}/acknowledge`, "Only a quiet finding can be acknowledged", "NOT_PENDING");
  patch(id, { in_digest: false, status: "resolved", resolved_at: nowIso() });
  await log("owner", `Acknowledged: ${f.title}`, { agent: "costs" });
}

resetCosts();
