/**
 * Deterministic financial engine for mock mode, the same formulas as the backend's
 * (services/api domain/proof/engine.py, docs/PROOF.md §5.7): integer cents, half-up.
 * The agent picks a formula; these functions compute the numbers. Never the LLM.
 */

export const round2 = (v: number) => Math.round((v + Number.EPSILON) * 100) / 100;

/** Euros → integer cents, and back. */
const cents = (eur: number) => Math.round(eur * 100);
const euros = (c: number) => c / 100;
/** Half-up division of integers (positive or negative), as Decimal ROUND_HALF_UP. */
const divHalfUp = (num: number, den: number) => Math.sign(num) * Math.floor(Math.abs(num) / den + 0.5);

/** formula_id = "variance": delta = billed − previous; bps = delta × 10000 / previous. */
export function variance(previous: number, current: number) {
  const delta = cents(current) - cents(previous);
  return { monthly: euros(delta), pct: divHalfUp(delta * 10_000, cents(previous)) / 100 };
}

/**
 * formula_id = "vpi_threshold_indexation" (lease §4.2): the rent follows the index only when it moved
 * by at least the threshold since the last adjustment; otherwise the base amount stays.
 * vpiThresholdIndexation(3800, 1176, 1212, 500) → change 306 bps, not allowed, justified 3800.
 * Index values in tenths of a point (VPI 117.6 = 1176).
 */
export function vpiThresholdIndexation(baseAmount: number, baseIndex: number, currentIndex: number, thresholdBps: number) {
  const change = currentIndex - baseIndex;
  const allowed = Math.abs(change) * 10_000 >= thresholdBps * baseIndex;
  const justified = allowed ? euros(divHalfUp(cents(baseAmount) * currentIndex, baseIndex)) : baseAmount;
  return { changeBps: divHalfUp(change * 10_000, baseIndex), allowed, justified };
}

/** formula_id = "surcharge_rule": below `belowNetGoods` on or after `validFrom` → the surcharge, else 0. */
export function surchargeRule(netGoods: number, date: string, surcharge: number, belowNetGoods: number, validFrom: string) {
  const applies = date >= validFrom && netGoods < belowNetGoods;
  return { applies, surcharge: applies ? surcharge : 0 };
}

/** formula_id = "annualize": monthly × 12. */
export const annualize = (monthly: number) => euros(cents(monthly) * 12);

/** Monthly payments on `day` from `from` that fall inside [windowStart, windowStart + days). */
export function paymentsInWindow(from: string, day: number, windowStart: string, days: number) {
  const end = new Date(`${windowStart}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() + days);
  const out: string[] = [];
  const d = new Date(`${from.slice(0, 8)}${String(day).padStart(2, "0")}T00:00:00Z`);
  if (d.toISOString().slice(0, 10) < from) d.setUTCMonth(d.getUTCMonth() + 1);
  for (; d < end; d.setUTCMonth(d.getUTCMonth() + 1)) {
    const iso = d.toISOString().slice(0, 10);
    if (iso >= windowStart) out.push(iso);
  }
  return out;
}

/** formula_id = "fx_conversion": foreign amount × EUR rate. */
export const fxToEur = (amount: number, eurPerUnit: number) => round2(amount * eurPerUnit);
