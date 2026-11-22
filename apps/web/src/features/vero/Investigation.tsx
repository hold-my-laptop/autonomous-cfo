import { useState } from "react";
import { useDecideActions, useDocument } from "../../api/queries";
import type { ActionDecisions, EvidenceRef, Finding, FindingStatus, Formula, RecommendedAction, TraceStep } from "../../api/types";
import { Icon } from "../../components/Icon";
import { useToast } from "../../components/Toast";
import { formatDate } from "../../lib/format";
import { btnPrimary } from "../today/DecisionCard";
import { ClassificationBadge, classLabel, eur, eurDelta, PaperDoc, TOOL_ICON } from "./parts";

const PIPELINE: { status: FindingStatus; label: string }[] = [
  { status: "ingested", label: "Read" },
  { status: "extracted", label: "Extracted" },
  { status: "checked", label: "Checked" },
  { status: "investigating", label: "Investigating" },
  { status: "classified", label: "Classified" },
  { status: "pending_approval", label: "Your approval" },
  { status: "resolved", label: "Done" },
];
const MAX_TOOL_CALLS = 6;
const num = (v: number) => v.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pctText = (v: number) => `${v.toLocaleString("en-GB", { maximumFractionDigits: 2 })}%`;
const signedPct = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${pctText(Math.abs(v))}`;

export const isWorking = (f: Finding) => ["ingested", "extracted", "checked", "investigating"].includes(f.status);

/** The clause the finding rests on, e.g. "Lease §4.2", from its evidence. */
const clauseOf = (f: Finding) => f.evidence_refs.find((e) => e.kind === "clause")?.label;

/** One cost check: what changed → visible investigation → evidence → prepared actions → one tap. */
export function FindingView({ finding: f }: { finding: Finding }) {
  const statusIdx = PIPELINE.findIndex((p) => p.status === f.status);
  const li = f.line_item;
  const working = isWorking(f);
  const facts = [
    li.previous_value !== null && { k: li.unit === "month" ? "Paid until now" : "Expected", v: eur(li.previous_value) },
    li.current_value !== null && { k: li.unit === "month" ? "Asked for" : "Billed", v: eur(li.current_value), bad: true },
    li.previous_value !== null && {
      k: li.unit === "month" ? "Change a month" : "Change",
      v: `${eurDelta(li.monthly_variance)}${li.variance_pct !== null ? ` · ${signedPct(li.variance_pct)}` : ""}`,
      bad: true,
    },
  ].filter((c): c is { k: string; v: string; bad?: boolean } => !!c);

  return (
    <div className="space-y-4">
      <section aria-labelledby={`inv-h-${f.finding_id}`} className="liquid-glass-strong rounded-3xl p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-xs text-ink-soft">
              <Icon name="file" size={13} />
              <span className="truncate">{f.vendor}</span>
              {f.source_doc_id && <span className="font-mono text-[11px] text-ink-muted">· {f.source_doc_id}</span>}
            </p>
            <h2 id={`inv-h-${f.finding_id}`} className="mt-1.5 text-[19px] font-semibold leading-snug">
              {f.title}
            </h2>
          </div>
          <div className="flex flex-col items-end gap-1" aria-live="polite">
            {f.classification_history.length ? (
              <ClassificationBadge value={f.classification} large />
            ) : (
              <span className="rounded-full bg-sunk px-3 py-1 text-[13px] text-ink-soft">Checking…</span>
            )}
            {f.classification_history.length > 1 && (
              <span className="text-[11px] text-ink-muted">{f.classification_history.map(classLabel).join(" → ")}</span>
            )}
          </div>
        </div>

        {facts.length > 0 && (
          <dl className="glass-inset mt-4 grid rounded-xl" style={{ gridTemplateColumns: `repeat(${facts.length}, minmax(0, 1fr))` }}>
            {facts.map((c, i) => (
              <div key={c.k} className={`px-3 py-3 sm:px-4 ${i ? "border-l border-line" : ""}`}>
                <dt className="text-xs text-ink-soft">{c.k}</dt>
                <dd className={`num mt-0.5 text-[15px] font-semibold ${c.bad && statusIdx >= 2 ? "text-short" : ""}`}>
                  {statusIdx >= 1 ? c.v : "…"}
                </dd>
              </div>
            ))}
          </dl>
        )}

        <ol className="mt-4 flex flex-wrap gap-1.5" aria-label="Progress">
          {PIPELINE.map((p, i) => {
            const done = i < statusIdx || f.status === "resolved";
            const current = i === statusIdx && f.status !== "resolved";
            return (
              <li
                key={p.status}
                aria-current={current ? "step" : undefined}
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium ${
                  done ? "bg-navy text-on-hero" : current ? "bg-cream text-ask" : "bg-sunk text-ink-muted"
                }`}
              >
                {done && <Icon name="check" size={11} />}
                {current && working && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ask" aria-hidden="true" />}
                {p.label}
              </li>
            );
          })}
        </ol>
      </section>

      {f.classification === "escalated" && <Escalated finding={f} />}

      <Trace
        steps={f.investigation_trace}
        mode={f.investigation?.mode}
        fromRun={!!f.run_id}
        running={f.status === "investigating"}
        visible={statusIdx >= 3 || f.investigation_trace.length > 0}
      />

      {(f.financial_impact || f.recommended_actions.length > 0) && <Outcome key={f.finding_id} finding={f} />}
    </div>
  );
}

/** Needs evidence after an investigation: what Vero searched, and why it couldn't set a label. */
function Escalated({ finding: f }: { finding: Finding }) {
  const inv = f.investigation;
  return (
    <section className="liquid-glass-strong rounded-3xl p-5 sm:px-6">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold text-ask">
        <Icon name="search" size={12} /> Needs evidence
      </p>
      <p className="mt-1 text-[15px] font-semibold">Vero couldn't find a reason for this change. Nothing is changed until you look at it.</p>
      {inv && inv.problems.length > 0 && <p className="mt-1 text-[13px] text-ink-soft">{inv.problems.join(" · ")}</p>}
    </section>
  );
}

function Trace({ steps, mode, fromRun, running, visible }: { steps: TraceStep[]; mode?: "agent" | "fallback"; fromRun: boolean; running: boolean; visible: boolean }) {
  if (!visible) return null;
  const sorted = [...steps].sort((a, b) => a.step - b.step);
  return (
    <section aria-labelledby="trace-h" className="liquid-glass-strong rounded-3xl p-5 sm:p-7">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="trace-h" className="text-[20px] font-semibold">How Vero found out</h2>
        <p className="text-[11px] text-ink-soft">
          {sorted.length} of max {MAX_TOOL_CALLS} look-ups · read-only tools · maths by the deterministic engine
          {mode && ` · ${mode === "agent" ? "Vero's agent" : "Vero's own steps"}`}
        </p>
      </div>
      {!fromRun && sorted.length > 0 && (
        <p className="mt-1 text-[12px] text-ink-soft">Checked when your documents were loaded. These are the look-ups it made, in order.</p>
      )}
      <ol className="mt-5 space-y-2" aria-live="polite">
        {sorted.map((s, i) => (
          <li key={s.step}>
            {s.hypothesis && s.hypothesis !== sorted[i - 1]?.hypothesis && (
              <p className="mb-1.5 mt-3 text-[11px] font-semibold uppercase tracking-wide text-ink-muted first:mt-0">{s.hypothesis}</p>
            )}
            <div className="glass-inset flex gap-4 rounded-xl p-3 sm:p-4">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-navy text-sand">
                <Icon name={s.tool ? TOOL_ICON[s.tool] : "search"} size={15} />
              </span>
              <div className="min-w-0 flex-1 pt-0.5">
                <p className="text-[13px] font-semibold">
                  {s.step}. {s.label}
                </p>
                {s.result && <p className="mt-1 text-[13px] text-ink-soft">{s.result}</p>}
              </div>
            </div>
          </li>
        ))}
        {running && (
          <li className="glass-inset flex gap-4 rounded-xl p-3 sm:p-4">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-dashed border-line-strong">
              <span className="h-2 w-2 animate-pulse rounded-full bg-ask" aria-hidden="true" />
            </span>
            <p className="pt-1.5 text-[13px] text-ink-soft">Step {sorted.length + 1}: looking…</p>
          </li>
        )}
      </ol>
    </section>
  );
}

type Tile = { k: string; v: string; calc: string; good?: boolean; bad?: boolean };

/** The verdict in words, from the formula the backend recomputed. */
function verdict(f: Finding, formula: Formula | undefined) {
  const fi = f.financial_impact;
  const clause = clauseOf(f) ?? "your contract";
  if (formula?.formula_id === "vpi_threshold_indexation") {
    const indexLine = `The index went from ${formula.base_index.toLocaleString("en-GB")} to ${formula.current_index.toLocaleString("en-GB")}: ${pctText(formula.change_pct)} (demo index values).`;
    if (!formula.adjustment_allowed) {
      return {
        title: `Your lease allows no rent increase yet.${fi ? ` The landlord asks ${eur(fi.disputed_monthly)} a month too much.` : ""}`,
        body: `${clause}: the rent may only follow the consumer price index once it has moved at least ${pctText(formula.threshold_pct)} since the last adjustment. ${indexLine} So the rent stays ${eur(formula.justified)}.`,
        calc: `${pctText(formula.change_pct)} < ${pctText(formula.threshold_pct)}: stays ${num(formula.base_amount)}`,
      };
    }
    return {
      title: `Your lease allows ${eur(formula.justified)}.${fi ? ` The landlord asks ${eur(fi.disputed_monthly)} a month more.` : ""}`,
      body: `${clause}: once the consumer price index moved at least ${pctText(formula.threshold_pct)}, the rent follows it in the same proportion. ${indexLine}`,
      calc: `${num(formula.base_amount)} × ${formula.current_index} / ${formula.base_index}`,
    };
  }
  if (formula?.formula_id === "surcharge_rule") {
    return {
      title: formula.applies ? `The surcharge of ${eur(formula.surcharge)} is allowed.` : "The surcharge doesn't apply to this delivery.",
      body: `${clause}: deliveries under ${eur(formula.below_net_goods)} of goods carry a ${eur(formula.surcharge)} surcharge. This one had ${eur(formula.net_goods)}.`,
      calc: `${num(formula.net_goods)} ${formula.applies ? "<" : "≥"} ${num(formula.below_net_goods)}`,
    };
  }
  return {
    title: fi ? `The evidence allows ${eur(fi.justified)}. ${eur(fi.disputed_monthly)} is billed above that.` : "Vero prepared what to do next.",
    body: `Checked against ${clause}.`,
    calc: fi ? `${num(fi.billed)} − ${num(fi.justified)}` : "",
  };
}

function Outcome({ finding }: { finding: Finding }) {
  const fi = finding.financial_impact;
  const inv = finding.investigation;
  const v = verdict(finding, inv?.formula);
  const conflict = finding.classification === "conflict";

  return (
    <>
      <section
        aria-labelledby={`verdict-h-${finding.finding_id}`}
        className={`rounded-3xl border p-5 sm:p-6 ${conflict ? "border-short/25 bg-short-tint" : "border-line bg-surface"}`}
      >
        <h2 id={`verdict-h-${finding.finding_id}`} className="text-[20px] font-semibold leading-snug">
          {v.title}
        </h2>
        <p className="mt-1 text-[13px] text-ink-soft">{v.body}</p>
        {fi && (
          <>
            <dl className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-line sm:grid-cols-3 lg:grid-cols-5">
              {([
                { k: "Asked for", v: eur(fi.billed), calc: finding.source_doc_id ?? "" },
                { k: "Allowed", v: eur(fi.justified), calc: v.calc, good: true },
                { k: "Disputed / month", v: eur(fi.disputed_monthly), calc: `${num(fi.billed)} − ${num(fi.justified)}`, bad: true },
                fi.disputed_annual !== undefined && {
                  k: "Disputed / year",
                  v: eur(fi.disputed_annual),
                  calc: `${num(fi.disputed_monthly)} × 12`,
                  bad: true,
                },
                fi.disputed_in_window !== undefined && {
                  k: "In the next 13 weeks",
                  v: eur(fi.disputed_in_window),
                  calc: "payments inside the forecast window",
                  bad: true,
                },
              ] as (Tile | false)[])
                .filter((c): c is Tile => !!c)
                .map((c) => (
                  <div key={c.k} className="bg-surface px-4 py-3">
                    <dt className="text-xs text-ink-soft">{c.k}</dt>
                    <dd className={`num tight mt-0.5 text-lg font-semibold ${c.bad ? "text-short" : c.good ? "text-safe" : ""}`}>{c.v}</dd>
                    <dd className="break-words font-mono text-[10.5px] text-ink-muted">{c.calc}</dd>
                  </div>
                ))}
            </dl>
            {fi.disputed_annual !== undefined && (
              <p className="mt-2 text-[11px] text-ink-muted">The yearly amount assumes the billed amount would be paid for 12 months.</p>
            )}
          </>
        )}
        {inv?.note && (
          <p className="mt-3 text-[12px] text-ink-soft">
            <span className="font-semibold">Vero's note</span> (its own words, not a checked fact): {inv.note}
          </p>
        )}
      </section>

      <Evidence finding={finding} />
      <Downstream finding={finding} />
      {finding.recommended_actions.length > 0 && <Actions finding={finding} />}
    </>
  );
}

/** Side by side: the billing document and the evidence. Only refs with a document can be opened. */
function Evidence({ finding }: { finding: Finding }) {
  const [english, setEnglish] = useState(true);
  const openable = finding.evidence_refs.filter(
    (e, i, all): e is EvidenceRef & { doc_id: string } =>
      !!e.doc_id && e.doc_id !== finding.source_doc_id && all.findIndex((x) => x.doc_id === e.doc_id) === i,
  );
  const labelOnly = finding.evidence_refs.filter((e) => !e.doc_id && e.kind !== "cost_item");
  const [right, setRight] = useState<string | null>(
    openable.find((e) => e.kind === "clause")?.doc_id ?? openable[0]?.doc_id ?? null,
  );
  const left = useDocument(finding.source_doc_id);
  const rightDoc = useDocument(right);

  if (!finding.source_doc_id && openable.length === 0) return null;
  const pane = (q: typeof left) =>
    q.data ? (
      <PaperDoc doc={q.data} showEnglish={english} />
    ) : q.isError ? (
      <p className="rounded-3xl border border-line bg-surface p-5 text-[13px] text-ink-soft">Couldn't open this document.</p>
    ) : (
      <div className="h-64 animate-pulse rounded-3xl bg-sunk" />
    );

  return (
    <section aria-labelledby={`ev-h-${finding.finding_id}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id={`ev-h-${finding.finding_id}`} className="text-[20px] font-semibold">Evidence</h2>
        <div className="flex flex-wrap items-center gap-1">
          {openable.map((d) => (
            <button
              key={d.doc_id}
              type="button"
              aria-pressed={right === d.doc_id}
              onClick={() => setRight(d.doc_id)}
              className={`h-8 rounded-full px-3 text-xs font-medium ${right === d.doc_id ? "btn-primary" : "text-ink-soft hover:glass-inset"}`}
            >
              {d.label}
            </button>
          ))}
          <label className="ml-2 inline-flex items-center gap-1.5 text-xs text-ink-soft">
            <input type="checkbox" checked={english} onChange={(e) => setEnglish(e.target.checked)} className="accent-link" />
            English
          </label>
        </div>
      </div>
      <div className={`grid gap-4 ${finding.source_doc_id && right ? "md:grid-cols-2" : ""}`}>
        {finding.source_doc_id && pane(left)}
        {right && pane(rightDoc)}
      </div>
      {labelOnly.length > 0 && (
        <p className="mt-3 flex flex-wrap items-center gap-1.5 text-[12px] text-ink-soft">
          Also cited:
          {labelOnly.map((e) => (
            <span key={`${e.kind}-${e.ref ?? e.label}`} className="rounded-full bg-sunk px-2 py-0.5 text-[11px]">
              {e.label}
            </span>
          ))}
        </p>
      )}
    </section>
  );
}

/** What the decision does to the payment and the 13-week forecast. */
function Downstream({ finding }: { finding: Finding }) {
  const p = finding.payment;
  if (!p) return null;
  const rows: { where: string; what: string; effect: string }[] = [];
  const from = p.effective_from ? ` from ${formatDate(p.effective_from)}` : "";
  if (p.decision === "keep" && p.current_amount !== undefined) {
    rows.push({
      where: `Payment (${p.cost_ref})`,
      what: `Keep paying ${eur(p.current_amount)}${p.billed_amount !== undefined ? ` instead of ${eur(p.billed_amount)}` : ""}${from}`,
      effect: "No change",
    });
  } else if (p.decision === "update" && p.current_amount !== undefined && p.proposed_amount !== undefined) {
    rows.push({
      where: `Payment (${p.cost_ref})`,
      what: `${eur(p.current_amount)} → ${eur(p.proposed_amount)}${from}`,
      effect: `${eurDelta(p.proposed_amount - p.current_amount)} a payment`,
    });
  } else if (p.decision === "paid") {
    rows.push({ where: "Invoice", what: "Already paid on its date", effect: "No change" });
  }
  if (p.forecast) {
    rows.push({
      where: "13-week forecast",
      what: `${p.forecast.payments.length} ${p.forecast.payments.length === 1 ? "payment" : "payments"} in the window · lowest balance ${eur(p.forecast.lowest_before)} → ${eur(p.forecast.lowest_after)}`,
      effect: p.forecast.outflow_difference ? eurDelta(-p.forecast.outflow_difference) : "No change",
    });
  } else if (p.decision === "keep") {
    rows.push({ where: "13-week forecast", what: `Already plans ${p.current_amount !== undefined ? eur(p.current_amount) : "the current amount"}`, effect: "No change" });
  }
  const inWindow = finding.financial_impact?.disputed_in_window;
  if (inWindow) {
    rows.push({ where: "Disputed in the 13 weeks", what: "Billed above what the contract allows, not paid while the landlord explains", effect: eur(inWindow) });
  }
  if (!rows.length) return null;
  return (
    <section aria-labelledby={`prop-h-${finding.finding_id}`}>
      <h2 id={`prop-h-${finding.finding_id}`} className="mb-3 text-[20px] font-semibold">What changes downstream</h2>
      <div className="liquid-glass-strong overflow-x-auto rounded-3xl">
        <table className="w-full min-w-[520px] text-[13px]">
          <thead className="glass-inset border-b border-line text-left text-xs text-ink-soft">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium sm:px-5">Where</th>
              <th scope="col" className="px-4 py-2 font-medium">What</th>
              <th scope="col" className="px-4 py-2 text-right font-medium sm:pr-5">Effect</th>
            </tr>
          </thead>
          <tbody className="num">
            {rows.map((r) => (
              <tr key={r.where} className="border-t border-line first:border-t-0">
                <td className="px-4 py-3 font-medium sm:px-5">{r.where}</td>
                <td className="px-4 py-3 text-ink-soft">{r.what}</td>
                <td className="px-4 py-3 text-right sm:pr-5">{r.effect}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const statusTag: Record<Exclude<RecommendedAction["status"], "pending">, { label: string; cls: string }> = {
  approved: { label: "Approved (simulated)", cls: "bg-safe-tint text-safe" },
  rejected: { label: "Declined", cls: "bg-sunk text-ink-soft" },
  blocked: { label: "Not allowed by your rules", cls: "bg-short-tint text-short" },
  stale: { label: "Out of date", cls: "bg-sunk text-ink-soft" },
};

/** The prepared actions. Ticked ones are approved, unticked ones declined, in one tap. */
function Actions({ finding }: { finding: Finding }) {
  const toast = useToast();
  const decide = useDecideActions();
  const pending = finding.recommended_actions.filter((a) => a.status === "pending");
  const [selected, setSelected] = useState<Record<string, boolean>>(() => Object.fromEntries(pending.map((a) => [a.id, true])));
  const approve = pending.filter((a) => selected[a.id] !== false);
  const reject = pending.filter((a) => selected[a.id] === false);
  const open = pending.length > 0;

  const label =
    approve.length && reject.length
      ? `Approve ${approve.length}, decline ${reject.length}`
      : approve.length
        ? `Approve ${approve.length === pending.length && pending.length > 1 ? `all ${approve.length}` : approve.length} ${approve.length === 1 ? "action" : "actions"}`
        : `Decline ${reject.length === 1 ? "it" : `all ${reject.length}`}`;

  const submit = async () => {
    const decisions: ActionDecisions = Object.fromEntries([
      ...approve.map((a) => [a.id, "approve"] as const),
      ...reject.map((a) => [a.id, "reject"] as const),
    ]);
    try {
      await decide.mutateAsync(decisions);
      toast(
        approve.length
          ? `Approved ${approve.length} ${approve.length === 1 ? "action" : "actions"} (simulated). Nothing was sent to the bank or ${finding.vendor}.`
          : "Declined. Nothing changes.",
      );
    } catch {
      toast("Some of that didn't go through. The list shows what was decided; try the rest again.", "neutral");
    }
  };

  return (
    <section aria-labelledby={`act-h-${finding.finding_id}`} className="liquid-glass-strong rounded-3xl p-5 sm:p-6">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold text-ask">
        <span className="h-2 w-2 rounded-full bg-sand" aria-hidden="true" />
        {open ? "Needs your OK" : "Decided · simulated"}
      </p>
      <h2 id={`act-h-${finding.finding_id}`} className="mt-2 text-[20px] font-semibold">Prepared actions</h2>
      <ul className="glass-inset mt-3 divide-y divide-sand/40 rounded-xl">
        {finding.recommended_actions.map((a) => (
          <ActionRow
            key={a.id}
            a={a}
            checked={selected[a.id] !== false}
            onCheck={(v) => setSelected((s) => ({ ...s, [a.id]: v }))}
          />
        ))}
      </ul>
      {open && (
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" disabled={decide.isPending} onClick={submit} className={btnPrimary}>
            {decide.isPending ? "Working…" : label}
          </button>
          <span className="self-center text-xs text-ink-soft">
            Unticked actions are declined. Simulated: nothing is sent to the bank or {finding.vendor}.
          </span>
        </div>
      )}
    </section>
  );
}

function ActionRow({ a, checked, onCheck }: { a: RecommendedAction; checked: boolean; onCheck: (v: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const pending = a.status === "pending";
  const tag = a.status === "pending" ? null : statusTag[a.status];
  const l = a.letter;
  return (
    <li className="px-4 py-3">
      <label className="flex items-start gap-3">
        <input
          type="checkbox"
          disabled={!pending}
          checked={pending ? checked : a.status === "approved"}
          onChange={(e) => onCheck(e.target.checked)}
          className="mt-1 h-4 w-4 accent-link"
        />
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-semibold">
            {a.title}
            {tag && <span className={`ml-2 rounded-full px-2 py-0.5 text-[10px] font-semibold ${tag.cls}`}>{tag.label}</span>}
          </span>
          <span className="block text-xs text-ink-soft">{a.detail}</span>
        </span>
      </label>
      {l && (
        <div className="ml-7 mt-1">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            className="inline-flex h-7 items-center gap-1 text-xs font-medium text-link hover:underline"
          >
            <Icon name="mail" size={13} />
            Read the letter (German)
            <Icon name="chevron" size={13} className={open ? "rotate-180" : ""} />
          </button>
          {open && (
            <div className="glass-inset mt-2 rounded-xl border border-line p-4">
              <p className="text-[11px] text-ink-soft">
                To: {l.to} · {l.source === "model" ? "Drafted by Vero's agent, checked by the backend" : "Vero's standard letter"} · German only
              </p>
              <div lang={l.language}>
                <p className="mt-1 text-[13px] font-semibold">{l.subject}</p>
                <p className="mt-2 whitespace-pre-line text-[13px]">{l.body}</p>
              </div>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
