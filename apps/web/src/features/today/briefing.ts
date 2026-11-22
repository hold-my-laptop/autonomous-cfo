/**
 * What the Overview says, derived only from real data (forecast, decisions, cost checks).
 * No story is assumed: every sentence comes from a number or an item that exists.
 *
 * The floor is the lowest balance the owner allows. The real backend sets it negative (an overdraft
 * limit: "stay within the 5 000 € overdraft"); the mock sets it positive (a safety buffer to keep).
 * The words follow whichever it is.
 */
import type { Company, CostCheck, Decision, Forecast } from "../../api/types";
import { formatDate, formatEur, formatEurWhole } from "../../lib/format";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export interface Floor {
  value: number;
  kind: "overdraft" | "buffer";
  /** "your €5,000 overdraft limit" / "your €5,000 safety buffer" */
  phrase: string;
}

export function floorOf(company: Company): Floor {
  const v = company.safetyBufferEur;
  return v < 0
    ? { value: v, kind: "overdraft", phrase: `your ${formatEurWhole(-v)} overdraft limit` }
    : { value: v, kind: "buffer", phrase: `your ${formatEurWhole(v)} safety buffer` };
}

/** Days below the floor (counting arranged credit) and the first one. Exact from the backend when it sends them. */
export function pastFloor(f: Forecast, floor: Floor): { days: number; first?: string } {
  if (f.daysBelowFloor !== undefined) return { days: f.daysBelowFloor, first: f.firstBreachDate };
  const below = f.days.filter((d) => d.balanceEur + (f.overdraftLimitEur ?? 0) < floor.value);
  return { days: below.length, first: below[0]?.date };
}

export interface Attention {
  decisions: number;
  suggestions: number;
  costChanges: number;
}

/** "2 decisions need you. 1 cost change needs a look." or "Nothing needs you today." */
export function attentionLine(a: Attention): string {
  const parts = [
    a.decisions && `${plural(a.decisions, "decision needs", "decisions need")} you.`,
    a.costChanges && `${plural(a.costChanges, "cost change needs", "cost changes need")} a look.`,
    a.suggestions && `${plural(a.suggestions, "idea", "ideas")} to review.`,
  ].filter(Boolean);
  return parts.length ? parts.join(" ") : "Nothing needs you today.";
}

/** The headline and one supporting line: how the business is doing, and what needs the owner. */
export function headline(f: Forecast, company: Company, a: Attention): { title: string; sub: string } {
  const floor = floorOf(company);
  const low = `${formatEurWhole(f.lowestBalanceEur)} on ${formatDate(f.lowestDate)}`;

  // After "fast-forward" (mock): some weeks are real. Compare forecast and actual.
  const actual = f.weeks.filter((w) => w.isActual);
  if (actual.length) {
    const lowActual = actual.reduce((x, y) => ((y.lowestEur ?? y.balanceEur) < (x.lowestEur ?? x.balanceEur) ? y : x));
    return {
      title: `${actual.length} of 13 weeks have happened. Here's how the forecast held up.`,
      sub: `Bars show the real balance, the line shows the forecast from ${formatDate(f.madeOn)}. Lowest so far: ${formatEurWhole(
        lowActual.lowestEur ?? lowActual.balanceEur,
      )}.`,
    };
  }

  const past = pastFloor(f, floor);
  if (past.days > 0 && past.first) {
    const ready =
      a.decisions > 0
        ? `${plural(a.decisions, "proposal is", "proposals are")} ready for your OK below.`
        : "No proposal is ready for it yet.";
    const more = attentionLine({ ...a, decisions: 0 });
    return {
      title: `Your cash falls below ${floor.phrase} on ${formatDate(past.first)}.`,
      sub: `It reaches ${low}${f.overdraftLimitEur ? `, even with the ${formatEurWhole(f.overdraftLimitEur)} credit arranged` : ""}. ${ready}${
        more === "Nothing needs you today." ? "" : ` ${more}`
      }`,
    };
  }

  if (f.lowestBalanceEur < 0) {
    // Below zero, but never past the floor: inside the overdraft limit, or covered by arranged credit.
    const room = floor.kind === "overdraft" ? `, ${formatEurWhole(f.lowestBalanceEur - floor.value)} before your limit` : "";
    const covered = f.overdraftLimitEur ? `, covered by the ${formatEurWhole(f.overdraftLimitEur)} credit arranged (simulated)` : room;
    return {
      title:
        floor.kind === "overdraft"
          ? `Your cash stays inside ${floor.phrase} for the next 13 weeks.`
          : "Your cash is covered for the next 13 weeks.",
      sub: `It dips to ${low}${covered}. ${attentionLine(a)}`,
    };
  }

  return {
    title: "Your cash stays above zero for the next 13 weeks.",
    sub: `The lowest point is ${low}${
      floor.kind === "buffer" ? `, ${formatEurWhole(f.lowestBalanceEur - floor.value)} above your safety buffer` : ""
    }. ${attentionLine(a)}`,
  };
}

// ───────────────────────── What Vero noticed ─────────────────────────

/** Why an observation matters and what the owner can do, per kind (real backend items). */
const INSIGHT: Record<NonNullable<Decision["insightKind"]>, { why: string; todo: (decisions: number) => string }> = {
  DRIVER: {
    why: "This is what pulls your balance down in the forecast.",
    todo: (n) => (n > 0 ? "See the proposals under Needs your OK." : "Nothing to decide right now."),
  },
  RECURRING: {
    why: "Vero expects these payments on their usual dates and includes them in your forecast.",
    todo: () => "Nothing: Vero checks every new payment against them.",
  },
  FX_EXPOSURE: {
    why: "These transfers are fixed in Swiss francs, so what they cost you in euros moves with the exchange rate.",
    todo: () => "No action needed.",
  },
  BACKTEST: {
    why: "It shows how close Vero's forecast came to what really happened, so you can judge how far to trust it.",
    todo: () => "No action needed.",
  },
};

/** Real analysis items get a "why it matters / what to do" summary; mock items keep their own text. */
export function explainInsight(d: Decision, decisions: number): Decision {
  if (!d.insightKind) return d;
  const i = INSIGHT[d.insightKind];
  return { ...d, summary: `Why it matters: ${i.why} What to do: ${i.todo(decisions)}` };
}

const list = (xs: string[]) => (xs.length < 2 ? (xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);

/** A cost check as a row of "What Vero noticed" (same component as the other observations). */
export function costCheckRow(c: CostCheck, at: string): Decision {
  const change = c.deltaEur !== undefined && c.deltaEur !== 0 ? `${c.deltaEur > 0 ? "+" : "−"}${formatEur(Math.abs(c.deltaEur))}` : null;
  const amount = `The amount changed${change ? ` (${change})` : ""}`;
  const checked = c.evidence.length ? `Vero checked it against ${list(c.evidence)}` : "Vero checked it";
  const why =
    c.outcome === "matches"
      ? `${amount}. ${checked} and found the reason, so the change is justified.`
      : c.outcome === "conflict"
        ? `${amount}. ${checked}: it is more than your contract allows.`
        : `${amount}. ${checked} but found no reason for it yet.`;
  const todo = c.needsOwner
    ? "Vero needs your input on this one."
    : c.outcome === "matches"
      ? "Nothing: it can be paid as billed."
      : "Nothing: you already decided what to do.";
  return {
    id: `cost-${c.id}`,
    agent: "costs",
    autonomy: "analysis",
    execution: "simulated",
    status: "info",
    createdAt: at,
    headline: c.title,
    summary: `Why it matters: ${why} What to do: ${todo}`,
    facts: [],
    reasoning: [],
  };
}

/** One sentence for the "all clear" card: what Vero actually checked. */
export function checkedLine(costChecks: CostCheck[] | undefined): string {
  if (!costChecks?.length) return "Vero keeps watching your bank account and your forecast.";
  const fine = costChecks.filter((c) => !c.needsOwner).length;
  return fine === costChecks.length
    ? `Vero checked ${plural(costChecks.length, "bill that changed", "bills that changed")}: ${
        costChecks.length === 1 ? "the change is handled" : "every change is handled"
      }.`
    : `Vero checked ${plural(costChecks.length, "bill that changed", "bills that changed")}: ${fine} handled.`;
}
