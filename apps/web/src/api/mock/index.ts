/**
 * Mock backend for Pizzeria Amalfi (Frankfurt am Main), replaying the official 2025 bank statement.
 * Demo time machine: "today" is Monday 14 April 2025. Agents only see transactions before that date.
 * Everything lives in memory; changes are pushed through mockBus exactly like the real backend pushes SSE.
 *
 * Demo story
 *  reset         Calm Monday 14 April. Cash agent: the June shutdown pushes the account into overdraft
 *                → overdraft facility (needs approval), CHF deferral (suggestion). Currency agent: fix the
 *                CHF rate. Tax agent: lower the June prepayment. Small things done for you.
 *  rent_notice   The landlord asks for +8% rent from 1 June → Vero checks it against the lease (see costs.ts).
 *  fast_forward  Jump to 1 July 2025: weeks up to 29 June become actuals, the forecast stays as a line.
 */
import type {
  Company,
  ContractCheckResult,
  ContractSimulation,
  Decision,
  DecisionAction,
  DemoScenario,
  Forecast,
  ForecastDay,
  ForecastWeek,
  Policy,
} from "../types";
import { mockBus } from "../mockBus";
import { formatDate, formatEur, formatEurWhole } from "../../lib/format";
import { clone, delay, getAuditEntries, log, minutesAgo, model, newId, nowIso, resetAudit, setDemoDate, status } from "./runtime";
import {
  ACCOUNT,
  type Adjustments,
  addDays,
  balanceOn,
  dailyBalances,
  forecastTxns,
  lowestPoint,
  overdraftInterest,
  round2,
  type Txn,
  TXNS,
  weeksFrom,
} from "./statement";
import * as costs from "./costs";

// ───────────────────────── Static data ─────────────────────────

const MADE_ON = "2025-04-14"; // the demo "today" and the day the forecast is made
const FAST_FORWARD_TO = "2025-07-01";
const BUFFER = 5_000; // demo value, editable later
const OVERDRAFT_RATE = 10; // % a year, example rate for the demo

const seen = TXNS.filter((t) => t.date < MADE_ON);

const policy: Policy = {
  autoApproveMaxEur: 1_000,
  canMoveFlexiblePayments: true,
  protectedCategories: ["salary", "social_security", "tax"],
  executionMode: "simulated",
};

// ───────────────────────── State ─────────────────────────

let asOf = MADE_ON;
let adj: Adjustments = {};
let overdraftLimit: number | undefined;
let decisions: Decision[] = [];
let forecast: Forecast;

function company(): Company {
  return {
    id: "pizzeria-amalfi",
    name: "Pizzeria Amalfi",
    iban: ACCOUNT,
    city: "Frankfurt am Main",
    employees: 3,
    ownerFirstName: "Vincenzo",
    currency: "EUR",
    asOf,
    cashTodayEur: balanceOn(addDays(asOf, -1)),
    safetyBufferEur: BUFFER,
  };
}

// ───────────────────────── Forecast ─────────────────────────

const opening = balanceOn(addDays(MADE_ON, -1));
const liquidity = () => ({ bufferEur: BUFFER, overdraftLimitEur: overdraftLimit });

function baseline() {
  const txns = forecastTxns(MADE_ON, adj);
  return { txns, weeks: weeksFrom(MADE_ON, txns, opening, liquidity()), low: lowestPoint(MADE_ON, txns, opening) };
}

function weekOf(weeks: ForecastWeek[], iso: string) {
  return weeks.find((w) => iso >= w.startDate && iso <= addDays(w.startDate, 6)) ?? weeks[weeks.length - 1];
}

function makeForecast(): Forecast {
  const { weeks: planned, low } = baseline();
  let weeks = planned;
  if (asOf > MADE_ON) {
    // Fast-forward: weeks that ended before `asOf` show what really happened; the forecast stays as a line.
    const real = weeksFrom(MADE_ON, TXNS.filter((t) => t.date >= MADE_ON), opening, liquidity());
    weeks = planned.map((w, i) =>
      addDays(w.startDate, 6) < asOf ? { ...real[i], isActual: true, forecastEur: w.balanceEur } : { ...w, forecastEur: w.balanceEur },
    );
  }
  const short = weeks.find((w) => w.balanceEur < 0);
  // Daily balances: actual before the demo date, forecast from it on, with a likely range that widens
  // by €90 a day from €600 (demo range, computed here, never by the model).
  const actual = asOf > MADE_ON ? dailyBalances(MADE_ON, addDays(MADE_ON, 90), TXNS.filter((t) => t.date >= MADE_ON), opening) : null;
  const todayIdx = Math.max(0, low.series.findIndex((d) => d.date === asOf));
  const days: ForecastDay[] = low.series.map((d, i) => {
    if (i < todayIdx) return { date: d.date, balanceEur: actual ? actual[i].balance : d.balance };
    const spread = 600 + 90 * (i - todayIdx);
    return { date: d.date, balanceEur: d.balance, lowEur: round2(d.balance - spread), highEur: round2(d.balance + spread) };
  });
  return {
    generatedAt: nowIso(),
    madeOn: MADE_ON,
    horizonDays: 91,
    startingBalanceEur: opening,
    weeks,
    days,
    firstShortDate: short?.startDate,
    lowestBalanceEur: low.balance,
    lowestDate: low.date,
    lowestWeek: weekOf(weeks, low.date).week,
    spareCashEur: Math.max(0, round2(low.balance - BUFFER)),
    overdraftLimitEur: overdraftLimit,
  };
}

function setForecast() {
  forecast = makeForecast();
  mockBus.emit({ type: "forecast.updated", data: clone(forecast) });
}

// ───────────────────────── Numbers the agents use (all computed) ─────────────────────────

const sumCat = (list: Txn[], c: Txn["category"]) => -round2(list.filter((t) => t.category === c).reduce((s, t) => s + t.amountEur, 0));
const juneFixed = () => {
  const june = forecastTxns(MADE_ON).filter((t) => t.date.startsWith("2025-06"));
  return {
    rent: sumCat(june, "rent"),
    payroll: sumCat(june, "payroll"),
    social: sumCat(june, "social_security"),
    taxes: round2(sumCat(june, "wage_tax") + sumCat(june, "income_tax")),
    utilities: sumCat(june, "utilities"),
    chf: sumCat(june, "fx_transfer"),
    sales: -sumCat(june, "card_sales") - sumCat(june, "card_payout"),
  };
};

const chfSeen = seen.filter((t) => t.category === "fx_transfer");
const lastRate = chfSeen[chfSeen.length - 1].fxRate!;
const rates = chfSeen.map((t) => t.fxRate!);
const CHF_MONTHLY = -chfSeen[0].amountOriginal; // 4166.67
const remainingChf = 12 - chfSeen.length; // April..December = 9
const exposureChf = round2(remainingChf * CHF_MONTHLY);
const FORWARD_RATE = 1.041; // example rate for the demo

// ───────────────────────── Decisions ─────────────────────────

function base(agent: Decision["agent"], autonomy: Decision["autonomy"], seed: string) {
  return { agent, autonomy, execution: "simulated" as const, createdAt: nowIso(), model: model(seed) };
}

function initialDecisions(): Decision[] {
  const { weeks, low } = baseline();
  const firstNeg = weeks.find((w) => w.balanceEur < 0)!;
  const j = juneFixed();
  const juneOut = round2(j.rent + j.payroll + j.social + j.taxes + j.utilities + j.chf);
  const facility = Math.ceil(-low.balance / 5000) * 5000; // 25 000
  const negDays = low.series.filter((d) => d.balance < 0);
  const interest = overdraftInterest(low.series, OVERDRAFT_RATE);
  const lowWeek = weekOf(weeks, low.date);
  const chfRelief = round2(2 * CHF_MONTHLY * lastRate);
  const lastPayouts = seen.filter((t) => t.category === "card_payout").slice(-2);
  const lastSuppliers = (["food", "wine", "produce"] as const).map((c) => seen.filter((t) => t.category === c).at(-1)!);
  const incomeTax = seen.filter((t) => t.category === "income_tax").at(-1)!;
  const prior = incomeTax.reference.match(/€(\d+)k/)?.[1];
  const basis = prior ? `last year's profit of €${prior}k` : "last year's profit";

  return [
    {
      id: "liq_check",
      ...base("liquidity", "analysis", "liq_check"),
      status: "info",
      headline: "Checked your cash for 13 weeks: the June shutdown pushes you into overdraft.",
      summary: `Card sales stop on 1 June, but rent, payroll, social security and the CHF transfer go on. Your balance goes below zero in the week of ${formatDate(
        firstNeg.startDate,
      )} and reaches ${formatEurWhole(low.balance)} on ${formatDate(low.date)}.`,
      facts: [
        { label: "Cash today", value: formatEurWhole(opening) },
        { label: "Lowest point", value: formatEurWhole(low.balance), tone: "bad" },
        { label: "Below zero from", value: `Week of ${formatDate(firstNeg.startDate)}`, tone: "bad" },
      ],
      reasoning: [
        { kind: "analysis", title: "Read your bank statement", detail: `${seen.length} transactions from 1 January to 13 April 2025.` },
        { kind: "analysis", title: "June shutdown: no card sales", detail: `Card sales and card payouts in June: ${formatEur(j.sales)}.` },
        {
          kind: "calculation",
          title: "Fixed costs continue in June",
          detail: `Rent ${formatEur(j.rent)} + payroll ${formatEur(j.payroll)} + social security ${formatEur(j.social)} + taxes ${formatEur(j.taxes)} + energy ${formatEur(j.utilities)} + CHF transfer ${formatEur(j.chf)} = ${formatEur(juneOut)}.`,
        },
        { kind: "rule", title: "Compared every week with zero and your buffer", detail: `${weeks.filter((w) => w.balanceEur < 0).length} weeks close below zero, the first in the week of ${formatDate(firstNeg.startDate)}.`, passed: false },
      ],
    },
    {
      id: "liq_overdraft",
      ...base("liquidity", "approval_required", "liq_overdraft"),
      status: "pending",
      headline: `Arrange a ${formatEurWhole(facility)} overdraft facility before 1 June`,
      summary: `During the shutdown your balance falls to ${formatEurWhole(low.balance)} on ${formatDate(low.date)}. An arranged facility covers the gap until sales pick up again in July.`,
      amountEur: facility,
      facts: [
        { label: "Lowest balance", value: `${formatEurWhole(low.balance)} (week of ${formatDate(lowWeek.startDate)})`, tone: "bad" },
        { label: "Safety buffer", value: formatEurWhole(BUFFER) },
        { label: "Estimated interest", value: `≈ ${formatEurWhole(interest)}` },
      ],
      reasoning: [
        { kind: "analysis", title: "June shutdown: no card sales for a month", detail: `June card sales: ${formatEur(j.sales)}.` },
        { kind: "calculation", title: "Fixed costs in June", detail: `${formatEur(juneOut)}: rent, payroll, social security, taxes, energy and the CHF transfer to Luigi.` },
        { kind: "calculation", title: "Size of the gap", detail: `Lowest balance ${formatEur(low.balance)} on ${formatDate(low.date)}. Rounded up to the next €5,000: ${formatEurWhole(facility)}.` },
        { kind: "rule", title: "Payroll, social security and taxes can't be moved", detail: "They are protected by your settings.", passed: true },
        {
          kind: "calculation",
          title: "Cost of the facility",
          detail: `${negDays.length} days below zero, ${OVERDRAFT_RATE}% a year (example rate) on the daily negative balance ≈ ${formatEur(interest)}.`,
        },
      ],
      offer: {
        kind: "overdraft",
        name: "Overdraft facility (Kontokorrentkredit)",
        amountEur: facility,
        termWeeks: Math.ceil(negDays.length / 7),
        ratePct: OVERDRAFT_RATE,
        costEur: interest,
        note: "Example rate for the demo",
      },
      approveLabel: "Request overdraft",
      rejectLabel: "Not now",
      approvedMessage: "Overdraft request prepared. This is a simulation, nothing was sent to the bank.",
      rejectedMessage: "OK. Without a facility your account goes into an unarranged overdraft in June. I'll keep watching.",
      llmVerdict: "approve",
      ruleVerdict: "approve",
      agreed: true,
    },
    {
      id: "liq_chf",
      ...base("liquidity", "recommendation", "liq_chf"),
      status: "pending",
      headline: "Ask Luigi to move the June and July CHF transfers to September",
      summary: `Two transfers of ${CHF_MONTHLY.toLocaleString("en-GB")} CHF fall in the weakest weeks. Moving them to September eases the gap by about ${formatEurWhole(chfRelief)}.`,
      amountEur: chfRelief,
      facts: [
        { label: "Relief in June and July", value: `≈ ${formatEurWhole(chfRelief)}`, tone: "good" },
        { label: "Rate used", value: `${lastRate} EUR/CHF (last transfer)` },
      ],
      reasoning: [
        { kind: "analysis", title: "Silent partnership: 12 tranches in 2025", detail: `${CHF_MONTHLY.toLocaleString("en-GB")} CHF on the 15th of each month to Luigi Amalfi, Zurich.` },
        { kind: "calculation", title: "Two tranches in the gap", detail: `2 × ${CHF_MONTHLY.toLocaleString("en-GB")} CHF × ${lastRate} = ${formatEur(chfRelief)}.` },
        { kind: "recommendation", title: "September is safe again", detail: "Sales are back from July; the forecast stays positive by September." },
      ],
      draftMessage: {
        kind: "partner_request",
        to: "luigi@luigis-ristorante.example",
        subject: "Kapitalraten Juni und Juli – Verschiebung auf September?",
        body:
          "Lieber Luigi,\n\nim Juni ist die Pizzeria wegen Betriebsurlaub geschlossen. Wäre es möglich, die Raten für Juni und Juli " +
          `(je ${CHF_MONTHLY.toLocaleString("de-CH")} CHF) auf September zu verschieben? Ab Juli laufen die Umsätze wieder normal.\n\nViele Grüße\nVincenzo`,
        english: {
          subject: "June and July capital tranches – move to September?",
          body:
            "Dear Luigi,\n\nThe pizzeria is closed for the June holiday. Could we move the June and July tranches " +
            `(${CHF_MONTHLY.toLocaleString("en-GB")} CHF each) to September? Sales are back to normal from July.\n\nBest regards\nVincenzo`,
        },
      },
      approveLabel: "Send request",
      rejectLabel: "Not now",
      approvedMessage: "Request to Luigi prepared (simulated). The forecast now assumes the two transfers move to September.",
      rejectedMessage: "OK, the transfers stay on their dates.",
    },
    {
      id: "fx_forward",
      ...base("fx", "recommendation", "fx_forward"),
      status: "pending",
      headline: `Fix the rate for the remaining ${remainingChf} CHF transfers`,
      summary: `This year the rate moved between ${Math.min(...rates)} and ${Math.max(...rates)} EUR/CHF. Fixing it now makes the next ${remainingChf} transfers (${exposureChf.toLocaleString(
        "en-GB",
        { maximumFractionDigits: 0 },
      )} CHF) cost the same every month.`,
      amountEur: round2(exposureChf * FORWARD_RATE),
      facts: [
        { label: "Still to pay", value: `${exposureChf.toLocaleString("en-GB", { maximumFractionDigits: 0 })} CHF` },
        { label: "Worst case (at 2025 high)", value: formatEurWhole(exposureChf * Math.max(...rates)), tone: "bad" },
        { label: "Best case (at 2025 low)", value: formatEurWhole(exposureChf * Math.min(...rates)), tone: "good" },
      ],
      reasoning: [
        { kind: "analysis", title: "Rates paid this year", detail: chfSeen.map((t) => `${formatDate(t.date)}: ${t.fxRate}`).join(" · ") + " EUR/CHF" },
        { kind: "calculation", title: "Exposure", detail: `${remainingChf} × ${CHF_MONTHLY.toLocaleString("en-GB")} CHF = ${exposureChf.toLocaleString("en-GB")} CHF.` },
        {
          kind: "calculation",
          title: "Range in euros",
          detail: `${formatEur(exposureChf * Math.min(...rates))} to ${formatEur(exposureChf * Math.max(...rates))}. With a forward at ${FORWARD_RATE} (example): ${formatEur(exposureChf * FORWARD_RATE)}.`,
        },
      ],
      offer: {
        kind: "fx_forward",
        name: `FX forward, ${remainingChf} monthly dates`,
        amountEur: round2(exposureChf * FORWARD_RATE),
        termWeeks: 37,
        ratePct: FORWARD_RATE,
        note: "Example rate for the demo",
      },
      approveLabel: "Prepare FX forward",
      rejectLabel: "Not now",
      approvedMessage: "FX forward request prepared. This is a simulation, nothing was sent to the bank.",
      rejectedMessage: "OK. I'll keep paying at the day's rate and tell you if it moves a lot.",
    },
    {
      id: "tax_prepay",
      ...base("tax", "recommendation", "tax_prepay"),
      status: "pending",
      headline: `Ask the tax office to lower the June income-tax prepayment (${formatEurWhole(-incomeTax.amountEur)})`,
      summary: `The prepayment is based on ${basis}. With a month without sales, this year's profit will be lower, so you can ask for less.`,
      amountEur: -incomeTax.amountEur,
      facts: [
        { label: "Due 10 June", value: formatEur(-incomeTax.amountEur) },
        { label: "Sales in June", value: formatEur(j.sales), tone: "bad" },
      ],
      reasoning: [
        { kind: "analysis", title: "Quarterly prepayment", detail: `${formatEur(-incomeTax.amountEur)} on 10 March, June, September and December. Basis: ${basis}.` },
        { kind: "analysis", title: "June shutdown", detail: "No card sales for a month means a lower profit for 2025." },
        { kind: "recommendation", title: "Ask, don't skip", detail: "Only the tax office can lower it. Until it agrees, the forecast keeps the full amount." },
      ],
      draftMessage: {
        kind: "tax_office_request",
        to: "Finanzamt Frankfurt am Main (via ELSTER)",
        subject: "Antrag auf Herabsetzung der Einkommensteuer-Vorauszahlung 2025",
        body:
          "Sehr geehrte Damen und Herren,\n\nwegen der Betriebsschließung der Pizzeria Amalfi im Juni 2025 wird der Gewinn 2025 niedriger ausfallen als im Vorjahr. " +
          "Ich beantrage daher, die Einkommensteuer-Vorauszahlung zum 10.06.2025 zu senken.\n\nMit freundlichen Grüßen\nVincenzo Amalfi",
        english: {
          subject: "Request to lower the 2025 income-tax prepayment",
          body:
            "Dear Sir or Madam,\n\nBecause Pizzeria Amalfi closes in June 2025, this year's profit will be lower than last year's. " +
            "I therefore ask you to lower the income-tax prepayment due on 10 June 2025.\n\nKind regards\nVincenzo Amalfi",
        },
      },
      approveLabel: "Prepare request",
      rejectLabel: "Not now",
      approvedMessage: "Request prepared (simulated). The forecast changes once the tax office agrees.",
      rejectedMessage: "OK, the prepayment stays as it is.",
    },
    {
      id: "done_payouts",
      ...base("liquidity", "done", "done_payouts"),
      status: "done",
      resolvedBy: "agent",
      createdAt: minutesAgo(80),
      resolvedAt: minutesAgo(80),
      headline: `Matched ${lastPayouts.length} card payouts to your card sales`,
      summary: lastPayouts.map((t) => `${formatDate(t.date)}: ${formatEur(t.amountEur)} from Commerzbank Global Pay`).join(", ") + ".",
      facts: [],
      reasoning: [
        { kind: "rule", title: "Same payer as every week", detail: "Commerzbank Global Pay, DE62 7004 5050 0931 0137 81", passed: true },
        { kind: "rule", title: "Amounts in the usual range", detail: "Twice a week, as since January.", passed: true },
      ],
      llmVerdict: "approve",
      ruleVerdict: "approve",
      agreed: true,
    },
    {
      id: "done_suppliers",
      ...base("liquidity", "done", "done_suppliers"),
      status: "done",
      resolvedBy: "agent",
      createdAt: minutesAgo(55),
      resolvedAt: minutesAgo(55),
      headline: "Sorted last week's supplier payments into food, wine and fresh produce",
      summary: lastSuppliers.map((t) => `${t.counterparty} ${formatEur(-t.amountEur)}`).join(", ") + ".",
      facts: [],
      reasoning: [
        { kind: "rule", title: "Known suppliers, same accounts as always", detail: "Asimex, Fattoria La Vino, Central Wholesale Market.", passed: true },
        { kind: "rule", title: "Each under your €1,000 auto limit", detail: lastSuppliers.map((t) => formatEur(-t.amountEur)).join(", "), passed: true },
      ],
      llmVerdict: "approve",
      ruleVerdict: "approve",
      agreed: true,
    },
  ];
}

/** Extra effects when the owner approves something. */
const onApprove: Record<string, () => Promise<void>> = {
  liq_overdraft: async () => {
    status("liquidity", "Adding the overdraft facility to the forecast…", true);
    await delay(800);
    overdraftLimit = decisions.find((d) => d.id === "liq_overdraft")?.amountEur;
    setForecast();
    await log("system", `Forecast updated: June gap covered by a ${formatEurWhole(overdraftLimit ?? 0)} overdraft facility (simulated)`, { agent: "liquidity", autonomy: "analysis" });
    status("liquidity", "The June gap is covered", false);
  },
  liq_chf: async () => {
    status("liquidity", "Moving the June and July CHF transfers in the forecast…", true);
    await delay(800);
    adj = { ...adj, chfDeferred: true };
    setForecast();
    await log("system", "Forecast updated: June and July CHF transfers assumed in September", { agent: "liquidity", autonomy: "analysis" });
    status("liquidity", "Forecast updated", false);
  },
};

function upsert(d: Decision, isNew: boolean) {
  decisions = [d, ...decisions.filter((x) => x.id !== d.id)];
  mockBus.emit({ type: isNew ? "decision.created" : "decision.updated", data: clone(d) });
}

async function seed() {
  for (const d of [...initialDecisions()].reverse()) {
    await log("agent", d.headline, { agent: d.agent, autonomy: d.autonomy, execution: d.autonomy === "analysis" ? undefined : d.execution, decisionId: d.id, model: d.model });
  }
}

function resetState() {
  asOf = MADE_ON;
  setDemoDate(MADE_ON);
  adj = {};
  overdraftLimit = undefined;
  decisions = initialDecisions();
  forecast = makeForecast();
  resetAudit();
  costs.resetCosts();
}
resetState();
let seeded = seed();

// ───────────────────────── Mock API ─────────────────────────

export async function getCompany() { await delay(80); return company(); }
export async function getPolicy() { await delay(80); return clone(policy); }
export async function getDecisions() { await delay(120); return clone(decisions); }
export async function getForecast() { await delay(120); return clone(forecast); }
export async function getAudit() { await seeded; await delay(80); return clone(getAuditEntries()); }

export async function actOnDecision(id: string, action: DecisionAction) {
  await delay(350);
  const d = decisions.find((x) => x.id === id);
  if (!d) throw new Error(`Decision ${id} not found`);
  const updated: Decision = { ...d, status: action === "approve" ? "approved" : "rejected", resolvedAt: nowIso(), resolvedBy: "owner" };
  upsert(updated, false);
  const verb = action === "approve" ? d.approveLabel ?? "Approved" : d.rejectLabel ?? "Rejected";
  await log("owner", `${verb}: ${d.headline}`, { agent: d.agent, autonomy: d.autonomy, execution: d.execution, decisionId: id });
  if (action === "approve" && onApprove[id]) await onApprove[id]();
  return clone(updated);
}

// ───── Vero (cost check) ─────
export async function getFindings() { await delay(100); return costs.getFindings(); }
export async function getCostMemory() { await delay(100); return costs.getMemory(); }
export async function getCostDocuments() { await delay(80); return costs.getDocuments(); }
export async function getDigest() { await delay(100); return costs.getDigest(); }
export async function getDocument(id: string) { await delay(80); return costs.getDocument(id); }
export async function decideFinding(id: string, approve: string[], reject: string[]) { return costs.decideFinding(id, approve, reject); }
export async function acknowledgeFinding(id: string) { return costs.acknowledgeFinding(id); }
export async function uploadDocument(file: File) { return costs.uploadDocument(file); }
export async function getDocumentRun(id: string) { return costs.getDocumentRun(id); }

// ───── Contract check ─────

export async function uploadContract(file: File): Promise<ContractCheckResult> {
  status("contract", "Reading the document…", true);
  await delay(1200);
  status("contract", "Testing it on your 13-week forecast…", true);
  await delay(1500);
  status("contract", "Contract checked", false);

  // Demo catering contract: the pizzeria pays food and staff first, the client pays 60 days after the event.
  const terms = {
    counterparty: "Rhein-Main Event GmbH (demo)",
    valueEur: 12_000,
    advancePct: 0,
    paymentTermDays: 60,
    startDate: "2025-05-19",
    durationWeeks: 1,
    costEur: 5_000,
  };
  const paidOn = addDays(terms.startDate, terms.paymentTermDays);
  const extra = (advance: number): Txn[] => {
    const t = (date: string, amountEur: number): Txn => ({
      date, type: "SIMULATION", reference: "Catering contract", counterparty: terms.counterparty, amountEur, currency: "EUR", amountOriginal: amountEur, category: "other",
    });
    return [t("2025-04-22", advance), t(terms.startDate, -terms.costEur), t(paidOn, terms.valueEur - advance)].filter((x) => x.amountEur !== 0);
  };
  const { txns, weeks } = baseline();
  const run = (advance: number) => {
    const all = [...txns, ...extra(advance)];
    return { weeks: weeksFrom(MADE_ON, all, opening, liquidity()), low: lowestPoint(MADE_ON, all, opening) };
  };
  const asWritten = run(0);
  const advance = round2(terms.valueEur * 0.5);
  const safer = run(advance);
  const baseLow = Math.min(...dailyBalances(MADE_ON, addDays(MADE_ON, 90), txns, opening).map((d) => d.balance));

  const sim: ContractSimulation = {
    kind: "contract",
    contractId: newId("k"),
    fileName: file.name,
    documentId: newId("doc"),
    model: model(`contract-${file.name}`),
    extractedTerms: terms,
    baseline: weeks,
    withContract: asWritten.weeks,
    withSuggestion: safer.weeks,
    valleyWeek: weekOf(asWritten.weeks, asWritten.low.date).week,
    minBalanceEur: asWritten.low.balance,
    extraOverdraftEur: round2(baseLow - asWritten.low.balance),
    suggestion: `Ask for a 50% advance (${formatEurWhole(advance)}) when you sign. It pays for the food and staff up front, so the event doesn't make your June overdraft deeper.`,
    suggestedTerms: { advancePct: 50 },
    counterProposal: {
      kind: "counter_proposal",
      to: "events@rhein-main-event.example",
      subject: "Catering am 19. Mai – Zahlungsbedingungen",
      body:
        "Sehr geehrte Damen und Herren,\n\nvielen Dank für Ihren Auftrag. Gerne übernehmen wir das Catering am 19. Mai. " +
        `Da wir Lebensmittel und Personal vorab bezahlen, bitten wir um eine Anzahlung von 50 % (${formatEurWhole(advance)}) bei Vertragsunterzeichnung; ` +
        "den Rest zahlen Sie wie vereinbart 60 Tage nach der Veranstaltung.\n\nMit freundlichen Grüßen\nVincenzo Amalfi\nPizzeria Amalfi",
      english: {
        subject: "Catering on 19 May – payment terms",
        body:
          "Dear Sir or Madam,\n\nThank you for the order. We are happy to cater on 19 May. Because we pay for food and staff up front, " +
          `we ask for a 50% advance (${formatEurWhole(advance)}) on signing; the rest is paid 60 days after the event as agreed.\n\nKind regards\nVincenzo Amalfi\nPizzeria Amalfi`,
      },
    },
  };
  await log("agent", `Checked contract ${file.name}: lowest point ${formatEurWhole(sim.extraOverdraftEur)} deeper, suggested a 50% advance`, {
    agent: "contract",
    autonomy: "recommendation",
    model: sim.model,
  });
  return sim;
}

// ───────────────────────── Demo controls ─────────────────────────

export async function runDemo(scenario: DemoScenario) {
  switch (scenario) {
    case "reset": {
      resetState();
      seeded = seed();
      await seeded;
      mockBus.emit({ type: "company.updated", data: company() });
      status("system", "Watching your money", false);
      break;
    }

    case "rent_notice": {
      await costs.runRentNotice();
      break;
    }

    case "fast_forward": {
      status("system", "Fast-forwarding to 1 July 2025…", true);
      await delay(900);
      asOf = FAST_FORWARD_TO;
      setDemoDate(FAST_FORWARD_TO);
      setForecast();
      mockBus.emit({ type: "company.updated", data: company() });

      const real = dailyBalances(MADE_ON, addDays(asOf, -1), TXNS.filter((t) => t.date >= MADE_ON), opening);
      const low = real.reduce((a, b) => (b.balance < a.balance ? b : a));
      const actualWeeks = forecast.weeks.filter((w) => w.isActual);
      const covered = overdraftLimit !== undefined && -low.balance <= overdraftLimit;
      upsert(
        {
          id: "liq_review",
          ...base("liquidity", "analysis", "liq_review"),
          status: "info",
          headline: `Back to the forecast from 14 April: ${actualWeeks.length} weeks have happened`,
          summary: `The lowest balance so far was ${formatEurWhole(low.balance)} on ${formatDate(low.date)}. The bars now show what really happened, the line shows what was forecast.`,
          facts: [
            { label: "Lowest so far", value: formatEurWhole(low.balance), tone: "bad" },
            { label: "Cash on 30 June", value: formatEurWhole(balanceOn("2025-06-30")) },
          ],
          reasoning: [
            { kind: "analysis", title: "Compared forecast and bank statement", detail: `${actualWeeks.length} weeks, 14 April to 29 June.` },
            { kind: "analysis", title: "June shutdown as expected", detail: "No card sales in June, fixed costs paid on their usual dates." },
          ],
        },
        true,
      );
      upsert(
        covered
          ? {
              id: "liq_covered",
              ...base("liquidity", "done", "liq_covered"),
              status: "done",
              resolvedBy: "agent",
              resolvedAt: nowIso(),
              headline: `The overdraft facility covered the June gap`,
              summary: `Your balance went down to ${formatEurWhole(low.balance)}, inside the ${formatEurWhole(overdraftLimit!)} facility. No unarranged overdraft, no returned payments.`,
              facts: [
                { label: "Lowest balance", value: formatEurWhole(low.balance) },
                { label: "Facility", value: formatEurWhole(overdraftLimit!), tone: "good" },
              ],
              reasoning: [{ kind: "rule", title: "Stayed inside the limit", detail: `${formatEur(low.balance)} ≥ −${formatEur(overdraftLimit!)}`, passed: true }],
            }
          : {
              id: "liq_uncovered",
              ...base("liquidity", "analysis", "liq_uncovered"),
              status: "info",
              headline: "Without a facility, June ran on an unarranged overdraft",
              summary: `Your balance went down to ${formatEurWhole(low.balance)} with no facility in place. Unarranged overdrafts are expensive and payments can bounce.`,
              facts: [{ label: "Lowest balance", value: formatEurWhole(low.balance), tone: "bad" }],
              reasoning: [{ kind: "rule", title: "No arranged facility", detail: "The overdraft request was not approved before June.", passed: false }],
            },
        true,
      );
      await log("system", `Fast-forward to 1 July 2025: ${actualWeeks.length} weeks of actuals, lowest ${formatEurWhole(low.balance)}`, { agent: "liquidity", autonomy: "analysis" });
      status("system", "It's 1 July 2025. Showing forecast against what really happened.", false);
      break;
    }
  }
  return { ok: true as const };
}
