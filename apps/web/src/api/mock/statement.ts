/**
 * Pizzeria Amalfi bank statement 2025 (official challenge sample data), parsed in the browser for mock mode.
 * German online-banking export: ";" separated, quoted, decimal comma, dates DD.MM.YY.
 * CHF rows carry the EUR rate in the reference text ("@ 1.042 EUR/CHF"): EUR = CHF × rate.
 *
 * Everything here is deterministic arithmetic: no LLM ever computes a number.
 */
// "@samples" points at the repo-root data/ folder (see vite.config.ts).
import raw from "@samples/pizzeria-amalfi-bank-statement-2025-v6.csv?raw";
import type { ForecastWeek, ISODate, RiskLevel } from "../types";

export type Category =
  | "card_sales"      // daily debit-card settlements
  | "card_payout"     // Commerzbank Global Pay credit-card payouts
  | "rent"
  | "utilities"
  | "payroll"
  | "social_security"
  | "wage_tax"
  | "income_tax"
  | "trade_tax"
  | "food"            // Asimex Wholesale
  | "wine"            // Fattoria La Vino
  | "produce"         // Central Wholesale Market
  | "fx_transfer"     // CHF to Luigi Amalfi
  | "other";

export interface Txn {
  date: ISODate;
  type: string;
  reference: string;
  counterparty: string;
  amountEur: number;
  currency: "EUR" | "CHF";
  amountOriginal: number;
  fxRate?: number;
  category: Category;
}

export const OPENING_BALANCE = 2499.99; // 1 Jan 2025
export const ACCOUNT = "DE89 3704 0044 0532 0130 00";

export const round2 = (v: number) => Math.round((v + Number.EPSILON) * 100) / 100;

/** Splits one CSV line on ";" outside quotes; "" inside quotes is a literal quote. */
function splitLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (c === '"') quoted = false;
      else cur += c;
    } else if (c === '"') quoted = true;
    else if (c === ";") {
      out.push(cur);
      cur = "";
    } else cur += c;
  }
  out.push(cur);
  return out;
}

/** "-4.166,67" / "-4166,67" → -4166.67 */
const parseAmount = (s: string) => Number(s.replace(/\./g, "").replace(",", "."));
/** "13.04.25" → "2025-04-13" */
const parseDate = (s: string) => {
  const [d, m, y] = s.split(".");
  return `20${y}-${m}-${d}`;
};

function categorize(type: string, ref: string, who: string): Category {
  if (who.includes("Customers (Debit)")) return "card_sales";
  if (who === "Commerzbank Global Pay") return "card_payout";
  if (type === "RENT PAYMENT") return "rent";
  if (who.startsWith("City Energy")) return "utilities";
  if (type === "SALARY PAYMENT") return "payroll";
  if (ref.startsWith("Social Security")) return "social_security";
  if (ref.includes("Wage Tax")) return "wage_tax";
  if (ref.includes("Income Tax")) return "income_tax";
  if (ref.includes("Trade Tax")) return "trade_tax";
  if (who.startsWith("Asimex")) return "food";
  if (who.startsWith("Fattoria")) return "wine";
  if (who.startsWith("Central Wholesale")) return "produce";
  if (type === "INTERNATIONAL WIRE TRANSFER") return "fx_transfer";
  return "other";
}

export function parseStatement(text: string): Txn[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  const rows = lines.slice(1).map(splitLine);
  // The export is newest first; replay it oldest first, keeping the order within a day.
  return rows.reverse().map((c) => {
    const [, booking, , type, reference, counterparty, , , amount, currency] = c;
    const original = parseAmount(amount);
    const rate = currency === "CHF" ? Number(reference.match(/@\s*([\d.]+)\s*EUR\/CHF/)?.[1]) : undefined;
    return {
      date: parseDate(booking),
      type,
      reference,
      counterparty,
      amountOriginal: original,
      currency: currency === "CHF" ? "CHF" : "EUR",
      fxRate: rate,
      amountEur: rate ? round2(original * rate) : original,
      category: categorize(type, reference, counterparty),
    };
  });
}

export const TXNS: Txn[] = parseStatement(raw);

// ───────────── Dates ─────────────

export const addDays = (iso: ISODate, n: number) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};

// ───────────── Balances ─────────────

/** Closing balance at the end of `iso`. */
export function balanceOn(iso: ISODate, txns: Txn[] = TXNS, opening = OPENING_BALANCE): number {
  let b = opening;
  for (const t of txns) if (t.date <= iso) b += t.amountEur;
  return round2(b);
}

/** Closing balance per day from `from` to `to` (inclusive). */
export function dailyBalances(from: ISODate, to: ISODate, txns: Txn[], opening: number) {
  const days: { date: ISODate; balance: number; inflow: number; outflow: number }[] = [];
  let b = opening;
  let i = 0;
  const sorted = [...txns].sort((a, z) => a.date.localeCompare(z.date));
  while (i < sorted.length && sorted[i].date < from) b += sorted[i++].amountEur;
  for (let d = from; d <= to; d = addDays(d, 1)) {
    let inflow = 0;
    let outflow = 0;
    while (i < sorted.length && sorted[i].date === d) {
      const a = sorted[i++].amountEur;
      b += a;
      if (a >= 0) inflow += a;
      else outflow += a;
    }
    days.push({ date: d, balance: round2(b), inflow: round2(inflow), outflow: round2(outflow) });
  }
  return days;
}

export interface Liquidity {
  bufferEur: number;
  /** Arranged overdraft facility: weeks are judged on balance + limit. */
  overdraftLimitEur?: number;
}

export function statusFor(balance: number, l: Liquidity): RiskLevel {
  // With an arranged facility, what counts is the money you can still use: balance + limit.
  const available = balance + (l.overdraftLimitEur ?? 0);
  if (available < 0) return "red";
  if (available < l.bufferEur) return "yellow";
  return "green";
}

/** 13 Monday-to-Sunday weeks from `start`, built from a list of (real or projected) transactions. */
export function weeksFrom(start: ISODate, txns: Txn[], opening: number, l: Liquidity, n = 13): ForecastWeek[] {
  const days = dailyBalances(start, addDays(start, n * 7 - 1), txns, opening);
  return Array.from({ length: n }, (_, w) => {
    const slice = days.slice(w * 7, w * 7 + 7);
    const close = slice[slice.length - 1].balance;
    return {
      week: w + 1,
      startDate: slice[0].date,
      inflowEur: round2(slice.reduce((s, d) => s + d.inflow, 0)),
      outflowEur: round2(slice.reduce((s, d) => s + d.outflow, 0)),
      balanceEur: close,
      lowestEur: Math.min(...slice.map((d) => d.balance)),
      bufferEur: l.bufferEur,
      status: statusFor(close, l),
    };
  });
}

// ───────────── Forecast ─────────────

/** The owner's calendar: the pizzeria closes for June (zero card sales). */
export const PLANNED_CLOSURE = { from: "2025-06-01", to: "2025-06-30" };

export interface Adjustments {
  /** Luigi agreed to move the June and July CHF transfers to September. */
  chfDeferred?: boolean;
  /** New monthly rent from 1 June (after the rent check). */
  rentFromJune?: number;
}

/**
 * Mock stand-in for the backend forecast: the 2025 actuals from `asOf` on (labelled "Forecast" in the UI),
 * plus the effect of decisions the owner approved. The real backend computes this from history only.
 */
export function forecastTxns(asOf: ISODate, adj: Adjustments = {}): Txn[] {
  return TXNS.filter((t) => t.date >= asOf)
    .filter((t) => !(adj.chfDeferred && t.category === "fx_transfer" && (t.date.slice(5, 7) === "06" || t.date.slice(5, 7) === "07")))
    .map((t) =>
      adj.rentFromJune && t.category === "rent" && t.date >= "2025-06-01" ? { ...t, amountEur: -adj.rentFromJune } : t,
    );
}

/** Lowest daily balance in a projection (and its date). */
export function lowestPoint(asOf: ISODate, txns: Txn[], opening: number, days = 91) {
  const series = dailyBalances(asOf, addDays(asOf, days - 1), txns, opening);
  const low = series.reduce((a, b) => (b.balance < a.balance ? b : a));
  return { ...low, series };
}

/** Interest on the negative balance, day by day: Σ max(0, −balance) × rate / 365. */
export function overdraftInterest(series: { balance: number }[], ratePct: number) {
  return round2(series.reduce((s, d) => s + Math.max(0, -d.balance), 0) * (ratePct / 100 / 365));
}

/** Sum per category per calendar month, for transactions before `asOf`. */
export function monthlyTotals(asOf: ISODate, category: Category) {
  const out = new Map<string, number>();
  for (const t of TXNS) if (t.date < asOf && t.category === category) out.set(t.date.slice(0, 7), round2((out.get(t.date.slice(0, 7)) ?? 0) + t.amountEur));
  return out;
}
