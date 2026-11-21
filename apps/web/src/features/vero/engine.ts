/**
 * Deterministic financial engine ("Math is deterministic").
 * The agent picks a formula; these functions compute the numbers. Never the LLM.
 * Everything is rounded to cents (2 decimals).
 */

export const round2 = (v: number) => Math.round((v + Number.EPSILON) * 100) / 100;

export function variance(previous: number, current: number) {
  const monthly = round2(current - previous);
  return { monthly, pct: round2((monthly / previous) * 100) };
}

/**
 * formula_id = "cpi_capped_indexation": an increase may not exceed the index change.
 * cpiCappedIndexation(3800, 2.2, 4104) → justified 3883.60, disputed 220.40 / month, 2644.80 / year.
 */
export function cpiCappedIndexation(base: number, capPct: number, billed: number) {
  const justified = round2(base * (1 + capPct / 100));
  const disputedMonthly = round2(Math.max(0, billed - justified));
  return {
    justified,
    justifiedIncrease: round2(justified - base),
    disputedMonthly,
    disputedAnnual: round2(disputedMonthly * 12),
  };
}

/** formula_id = "fx_conversion": foreign amount × EUR rate. */
export const fxToEur = (amount: number, eurPerUnit: number) => round2(amount * eurPerUnit);
