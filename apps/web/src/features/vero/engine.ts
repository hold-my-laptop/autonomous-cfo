/**
 * Deterministic financial engine ("Math is deterministic"), the same formulas as the backend's
 * proof engine (docs/PROOF.md §5.7). The agent picks a formula; these functions compute the numbers.
 * Never the LLM. Money is rounded to cents (2 decimals), basis points half-up.
 */

export const round2 = (v: number) => Math.round((v + Number.EPSILON) * 100) / 100;

export function variance(previous: number, current: number) {
  const monthly = round2(current - previous);
  return { monthly, pct: round2((monthly / previous) * 100) };
}

/**
 * formula_id = "vpi_threshold_indexation" (lease §4.2): the rent may follow the consumer price index
 * only once the index has moved at least `thresholdBps` since the last adjustment.
 * vpiThresholdIndexation(3800, 117.6, 121.2, 500, 4104) → change 306 bps, not allowed,
 * justified 3800, disputed 304 a month, 3648 a year.
 */
export function vpiThresholdIndexation(base: number, baseIndex: number, currentIndex: number, thresholdBps: number, billed: number) {
  const changeBps = Math.round(((currentIndex - baseIndex) * 10000) / baseIndex);
  const allowed = changeBps >= thresholdBps;
  const justified = allowed ? round2((base * currentIndex) / baseIndex) : base;
  const disputedMonthly = round2(Math.max(0, billed - justified));
  return { changeBps, allowed, justified, disputedMonthly, disputedAnnual: round2(disputedMonthly * 12) };
}

/** formula_id = "fx_conversion": foreign amount × EUR rate. */
export const fxToEur = (amount: number, eurPerUnit: number) => round2(amount * eurPerUnit);
