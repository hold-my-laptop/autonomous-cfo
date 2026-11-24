/**
 * How a checked cost change is told: the verdict, what changes downstream and the trace.
 * The live adapter (from GET /findings and GET /runs/{id}) and the mock both build their
 * findings with these, so both modes tell the same story with the same words.
 */
import type { Propagation, TraceStep, Verdict } from "../../api/types";
import { formatDate, formatEur } from "../../lib/format";

const num = (v: number) => v.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (bps: number) => `${(bps / 100).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} %`;
const idx = (v: number) => v.toLocaleString("en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** "s4_2" → "§4.2" */
export const clauseName = (itemId?: string | null) => (itemId && /^s\d/.test(itemId) ? `§${itemId.slice(1).replace(/_/g, ".")}` : itemId ?? "");

export interface IndexRentFacts {
  /** "§4.2" */
  clause: string;
  baseIndex: number;
  currentIndex: number;
  thresholdBps: number;
  changeBps: number;
  allowed: boolean;
  current: number;
  billed: number;
  justified: number;
  disputed: number;
  disputedAnnual: number;
  disputedInWindow?: number;
  /** Payments inside the 13-week window, e.g. RENT-06, RENT-07, RENT-08. */
  payments: string[];
  effectiveFrom: string;
  sourceDoc: string | null;
}

/** Lease §4.2: the rent follows the price index only past a threshold (docs/PROOF.md §7.1). */
export function indexRentVerdict(x: IndexRentFacts): Verdict {
  const threshold = pct(x.thresholdBps);
  const moved = `The index moved ${pct(x.changeBps)} (${idx(x.baseIndex)} → ${idx(x.currentIndex)})`;
  const title = x.allowed
    ? `Your lease lets the rent follow the price index: at most ${formatEur(x.justified)}. The landlord asks ${formatEur(x.disputed)} a month more.`
    : `Your lease only allows a rent change once the price index has moved ${threshold}. It moved ${pct(x.changeBps)}, so the rent stays ${formatEur(x.justified)}.`;
  const detail = `The landlord bills ${formatEur(x.billed)} from ${formatDate(x.effectiveFrom)} and names no index or clause. ${moved}; ${x.clause} needs at least ${threshold}.`;
  const cells: Verdict["cells"] = [
    { k: "Billed", v: formatEur(x.billed), calc: x.sourceDoc ? `${x.sourceDoc}, p. 1` : "the bill" },
    {
      k: "Allowed",
      v: formatEur(x.justified),
      calc: x.allowed ? `${num(x.current)} × ${idx(x.currentIndex)} / ${idx(x.baseIndex)}` : `${pct(x.changeBps)} < ${threshold}: no change`,
      tone: "good",
    },
    { k: "Disputed / month", v: formatEur(x.disputed), calc: `${num(x.billed)} − ${num(x.justified)}`, tone: "bad" },
    { k: "Disputed / year", v: formatEur(x.disputedAnnual), calc: `${num(x.disputed)} × 12`, tone: "bad" },
  ];
  if (x.disputedInWindow !== undefined) {
    cells.push({
      k: "In the next 13 weeks",
      v: formatEur(x.disputedInWindow),
      calc: x.payments.length ? `${x.payments.length} payments × ${num(x.disputed)}` : "inside the forecast window",
      tone: "bad",
    });
  }
  return {
    title,
    detail,
    cells,
    note: "The index values are demo values. Disputed per year assumes the billed rent continues for 12 months.",
  };
}

/** KEEP: keep paying the current amount; UPDATE: pay the proposed amount from a date. */
export function paymentPropagation(x: {
  decision: "KEEP" | "UPDATE" | "PAID";
  label: string;
  current: number;
  proposed: number;
  billed: number;
  effectiveFrom?: string | null;
  payments: string[];
  outflowDifference: number;
  lowestBefore?: number;
  lowestAfter?: number;
}): Propagation[] {
  const from = x.effectiveFrom ? ` from ${formatDate(x.effectiveFrom)}` : "";
  const rows: Propagation[] = [
    {
      id: "payments",
      where:
        x.decision === "KEEP"
          ? `${x.label} payments${from}: stay at ${formatEur(x.current)}`
          : x.decision === "UPDATE"
            ? `${x.label} payments${from}: ${formatEur(x.current)} → ${formatEur(x.proposed)}`
            : `${x.label}: already paid`,
      note: x.decision === "KEEP" ? `Not the ${formatEur(x.billed)} billed. Pay what the contract allows and ask in writing.` : "Once you approve.",
      change: x.decision === "PAID" ? 0 : x.proposed - x.current,
    },
  ];
  if (x.payments.length) {
    const lowest =
      x.lowestBefore !== undefined && x.lowestAfter !== undefined
        ? x.lowestBefore === x.lowestAfter
          ? ` Lowest balance stays ${formatEur(x.lowestBefore)}.`
          : ` Lowest balance ${formatEur(x.lowestBefore)} → ${formatEur(x.lowestAfter)}.`
        : "";
    rows.push({
      id: "forecast",
      where: "13-week forecast",
      note: `${x.payments.join(", ")}.${lowest}`,
      change: x.outflowDifference,
    });
  }
  return rows;
}

/**
 * A run's trace (GET /runs/{id}) as tool calls: each TOOL_CALL is a step, under the hypothesis
 * (INVESTIGATE) it tests, with the TOOL_RESULT that follows it.
 */
export function traceSteps(trace: { kind: string; label: string }[]): TraceStep[] {
  const steps: TraceStep[] = [];
  let hypothesis = "";
  for (const t of trace) {
    if (t.kind === "INVESTIGATE") hypothesis = t.label.replace(/^H\d+\s*·\s*/, "");
    else if (t.kind === "TOOL_CALL") steps.push({ step: steps.length + 1, hypothesis, call: t.label, result: "", evidence_ref: null, tool: toolOf(t.label) });
    else if (t.kind === "TOOL_RESULT" && steps.length) steps[steps.length - 1].result = t.label;
  }
  return steps;
}

/** The preloaded findings have no run: their investigation lists what was looked up, in order. */
export const searchedSteps = (searched: string[]): TraceStep[] =>
  searched.map((call, i) => ({ step: i + 1, hypothesis: "", call, result: "", evidence_ref: null, tool: toolOf(call) }));

function toolOf(call: string): TraceStep["tool"] {
  if (/inbox/i.test(call)) return "search_inbox";
  if (/^Reading/i.test(call)) return "read_clause";
  if (/index|Looking up/i.test(call)) return "get_reference_index";
  if (/^Comput|^Checking/i.test(call)) return "compute";
  return "search_documents";
}
