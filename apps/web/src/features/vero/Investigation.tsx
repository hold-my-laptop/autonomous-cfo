import { useState } from "react";
import { Link } from "react-router-dom";
import { useApproveFinding, useDocument } from "../../api/queries";
import type { Finding, FindingStatus, RecommendedAction, TraceStep } from "../../api/types";
import { Icon } from "../../components/Icon";
import { useToast } from "../../components/Toast";
import { formatDate } from "../../lib/format";
import { btnPrimary } from "../today/DecisionCard";
import { ModelLine } from "../today/Reasoning";
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

export const isWorking = (f: Finding) => ["ingested", "extracted", "checked", "investigating"].includes(f.status);

/** One cost check: what changed → visible investigation → evidence → prepared actions → one tap. */
export function FindingView({ finding: f }: { finding: Finding }) {
  const statusIdx = PIPELINE.findIndex((p) => p.status === f.status);
  const li = f.line_item;
  const working = isWorking(f);

  return (
    <div className="space-y-4">
      <section aria-labelledby={`inv-h-${f.finding_id}`} className="liquid-glass-strong rounded-3xl p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-xs text-ink-soft">
              <Icon name="mail" size={13} />
              <span className="truncate">{f.vendor}</span>
              <span className="font-mono text-[11px] text-ink-muted">· {f.source_doc_id}</span>
            </p>
            <h2 id={`inv-h-${f.finding_id}`} className="mt-1.5 text-[19px] font-semibold leading-snug">
              {li.description} · from {formatDate(li.effective_from)}
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

        <dl className="glass-inset mt-4 grid grid-cols-3 rounded-xl">
          {[
            { k: "Paid until now", v: eur(li.previous_value) },
            { k: "Asked for", v: eur(li.current_value), bad: true },
            { k: "Change", v: `${eurDelta(li.monthly_variance)} · +${li.variance_pct.toLocaleString("en-GB")}%`, bad: true },
          ].map((c, i) => (
            <div key={c.k} className={`px-3 py-3 sm:px-4 ${i ? "border-l border-line" : ""}`}>
              <dt className="text-xs text-ink-soft">{c.k}</dt>
              <dd className={`num mt-0.5 text-[15px] font-semibold ${c.bad && statusIdx >= 2 ? "text-short" : ""}`}>
                {statusIdx >= 1 ? c.v : "…"}
              </dd>
            </div>
          ))}
        </dl>

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

      {f.evidence_request && (
        <section className="liquid-glass-strong flex flex-col gap-3 rounded-3xl p-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <div>
            <p className="flex items-center gap-1.5 text-[11px] font-semibold text-ask">
              <Icon name="search" size={12} /> Needs evidence
            </p>
            <p className="mt-1 text-[15px] font-semibold">{f.evidence_request.message}</p>
          </div>
          <Link to={f.evidence_request.href} className={`${btnPrimary} shrink-0`}>
            {f.evidence_request.action_label}
          </Link>
        </section>
      )}

      <Trace
        steps={f.investigation_trace}
        running={f.status === "investigating"}
        visible={statusIdx >= 3 || f.investigation_trace.length > 0}
      />

      {f.financial_impact && <Outcome key={f.finding_id} finding={f} />}
    </div>
  );
}

function Trace({ steps, running, visible }: { steps: TraceStep[]; running: boolean; visible: boolean }) {
  if (!visible) return null;
  const sorted = [...steps].sort((a, b) => a.step - b.step);
  return (
    <section aria-labelledby="trace-h" className="liquid-glass-strong rounded-3xl p-5 sm:p-7">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="trace-h" className="text-[20px] font-semibold">How Vero found out</h2>
        <p className="text-[11px] text-ink-soft">
          {sorted.length} of max {MAX_TOOL_CALLS} tool calls · read-only tools · maths by the deterministic engine
        </p>
      </div>
      <ol className="mt-5 space-y-2" aria-live="polite">
        {sorted.map((s) => (
          <li key={s.step} className="glass-inset flex gap-4 rounded-xl p-3 sm:p-4">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-navy text-sand">
              <Icon name={TOOL_ICON[s.tool]} size={15} />
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <p className="text-[13px] font-semibold">
                {s.step}. {s.hypothesis}
              </p>
              <p className="mt-1 break-words font-mono text-[11px] text-ask">
                {s.tool}({Object.entries(s.input).map(([k, v]) => `${k}: ${typeof v === "string" ? `"${v}"` : v}`).join(", ")})
              </p>
              <p className="mt-1.5 text-[13px] text-ink-soft">{s.result}</p>
              {s.evidence_ref && <p className="mt-1 font-mono text-[11px] text-ink-soft">evidence → {s.evidence_ref}</p>}
              {s.model && <ModelLine model={s.model} className="mt-1" />}
            </div>
          </li>
        ))}
        {running && (
          <li className="glass-inset flex gap-4 rounded-xl p-3 sm:p-4">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-dashed border-line-strong">
              <span className="h-2 w-2 animate-pulse rounded-full bg-ask" aria-hidden="true" />
            </span>
            <p className="pt-1.5 text-[13px] text-ink-soft">Step {sorted.length + 1}: calling a tool…</p>
          </li>
        )}
      </ol>
    </section>
  );
}

function Outcome({ finding }: { finding: Finding }) {
  const toast = useToast();
  const approve = useApproveFinding();
  const fi = finding.financial_impact!;
  const li = finding.line_item;
  const resolved = finding.status === "resolved";
  const capPct = Number(finding.investigation_trace.find((t) => t.tool === "compute")?.input.cap_pct ?? 0);
  const capFactor = (1 + capPct / 100).toLocaleString("en-GB", { minimumFractionDigits: 3 });

  const rightOptions = finding.evidence_refs.filter((e) => e.doc_id !== finding.source_doc_id);
  const [right, setRight] = useState(rightOptions.find((e) => e.type === "lease")?.doc_id ?? rightOptions[0]?.doc_id ?? null);
  const [english, setEnglish] = useState(true);
  const left = useDocument(finding.source_doc_id);
  const rightDoc = useDocument(right);

  const [selected, setSelected] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(finding.recommended_actions.map((a) => [a.type, true])),
  );
  const chosen = finding.recommended_actions.filter((a) => selected[a.type]).map((a) => a.type);
  const total = finding.recommended_actions.length;

  return (
    <>
      <section aria-labelledby="verdict-h" className="rounded-3xl border border-short/25 bg-short-tint p-5 sm:p-6">
        <h2 id="verdict-h" className="text-[20px] font-semibold leading-snug">
          Your lease ties the rent to the price index. The landlord asks {eur(fi.disputed_amount_monthly)} a month too much.
        </h2>
        <p className="mt-1 text-[13px] text-ink-soft">
          The notice gives no index or clause for +{li.variance_pct.toLocaleString("en-GB")}%. §5 allows the change of the German consumer
          price index: {capPct.toLocaleString("en-GB")}% (demo dataset value).
        </p>
        <dl className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-line sm:grid-cols-4">
          {[
            { k: "Asked for", v: eur(li.current_value), calc: `${finding.source_doc_id}, p. 1` },
            { k: "Justified", v: eur(fi.justified_value), calc: `${num(li.previous_value)} × ${capFactor}`, good: true },
            { k: "Disputed / month", v: eur(fi.disputed_amount_monthly), calc: `${num(li.current_value)} − ${num(fi.justified_value)}`, bad: true },
            { k: "Disputed / year", v: eur(fi.disputed_amount_annual), calc: `${num(fi.disputed_amount_monthly)} × 12`, bad: true },
          ].map((c) => (
            <div key={c.k} className="bg-surface px-4 py-3">
              <dt className="text-xs text-ink-soft">{c.k}</dt>
              <dd className={`num tight mt-0.5 text-lg font-semibold ${c.bad ? "text-short" : c.good ? "text-safe" : ""}`}>{c.v}</dd>
              <dd className="font-mono text-[10.5px] text-ink-muted">{c.calc}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-[11px] text-ink-muted">{fi.assumption}.</p>
      </section>

      <section aria-labelledby="ev-h">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 id="ev-h" className="text-[20px] font-semibold">Evidence</h2>
          <div className="flex flex-wrap items-center gap-1">
            {rightOptions.map((d) => (
              <button
                key={d.doc_id}
                type="button"
                aria-pressed={right === d.doc_id}
                onClick={() => setRight(d.doc_id)}
                className={`h-8 rounded-full px-3 text-xs font-medium ${right === d.doc_id ? "btn-primary" : "text-ink-soft hover:glass-inset"}`}
              >
                {d.type === "lease" ? `Lease ${d.clause ?? ""}` : d.type === "statement" ? "Bank statement" : d.doc_id}
              </button>
            ))}
            <label className="ml-2 inline-flex items-center gap-1.5 text-xs text-ink-soft">
              <input type="checkbox" checked={english} onChange={(e) => setEnglish(e.target.checked)} className="accent-link" />
              English
            </label>
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {left.data ? <PaperDoc doc={left.data} showEnglish={english} /> : <div className="h-64 animate-pulse rounded-3xl bg-sunk" />}
          {rightDoc.data ? <PaperDoc doc={rightDoc.data} showEnglish={english} /> : <div className="h-64 animate-pulse rounded-3xl bg-sunk" />}
        </div>
      </section>

      <section aria-labelledby="prop-h">
        <h2 id="prop-h" className="mb-3 text-[20px] font-semibold">What changes downstream</h2>
        <div className="liquid-glass-strong overflow-x-auto rounded-3xl">
          <table className="w-full min-w-[520px] text-[13px]">
            <thead className="glass-inset border-b border-line text-left text-xs text-ink-soft">
              <tr>
                <th scope="col" className="px-4 py-2 font-medium sm:px-5">Where</th>
                <th scope="col" className="px-4 py-2 font-medium">Note</th>
                <th scope="col" className="px-4 py-2 text-right font-medium sm:pr-5">Change a month</th>
              </tr>
            </thead>
            <tbody className="num">
              {finding.propagation.map((p) => (
                <tr key={p.id} className="border-t border-line first:border-t-0">
                  <td className="px-4 py-3 font-medium sm:px-5">
                    {p.target === "standing_order" ? `Rent standing order: ${eur(p.old)} → ${eur(p.new)}` : `13-week forecast, from ${formatDate(p.from)}`}
                  </td>
                  <td className="px-4 py-3 text-ink-soft">{p.note}</td>
                  <td className="px-4 py-3 text-right sm:pr-5">{eurDelta(p.target === "standing_order" ? p.new - p.old : p.delta_monthly)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="act-h" className="liquid-glass-strong rounded-3xl p-5 sm:p-6">
        <p className="flex items-center gap-1.5 text-[11px] font-semibold text-ask">
          <span className="h-2 w-2 rounded-full bg-sand" aria-hidden="true" />
          {resolved ? "Approved · simulated" : "Needs your OK"}
        </p>
        <h2 id="act-h" className="mt-2 text-[20px] font-semibold">Prepared actions</h2>
        <ul className="glass-inset mt-3 divide-y divide-sand/40 rounded-xl">
          {finding.recommended_actions.map((a) => (
            <ActionRow key={a.type} a={a} resolved={resolved} checked={!!selected[a.type]} onCheck={(v) => setSelected((s) => ({ ...s, [a.type]: v }))} />
          ))}
        </ul>
        {!resolved && (
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={chosen.length === 0 || approve.isPending}
              onClick={async () => {
                try {
                  await approve.mutateAsync({ id: finding.finding_id, actions: chosen });
                  toast(`Approved ${chosen.length} ${chosen.length === 1 ? "action" : "actions"} (simulated). Nothing was sent to the bank or the landlord.`);
                } catch {
                  toast("That didn't go through. Check the connection and try again.", "neutral");
                }
              }}
              className={btnPrimary}
            >
              {approve.isPending
                ? "Working…"
                : `Approve ${chosen.length === total ? `all ${total}` : chosen.length} ${chosen.length === 1 ? "action" : "actions"}`}
            </button>
            <span className="self-center text-xs text-ink-soft">Simulated: nothing is sent to the bank or the landlord.</span>
          </div>
        )}
      </section>
    </>
  );
}

function ActionRow({ a, resolved, checked, onCheck }: { a: RecommendedAction; resolved: boolean; checked: boolean; onCheck: (v: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const [lang, setLang] = useState<"de" | "en">("de");
  const d = a.draft;
  const text = d && lang === "en" && d.english ? d.english : d;
  return (
    <li className="px-4 py-3">
      <label className="flex items-start gap-3">
        <input type="checkbox" disabled={resolved} checked={checked} onChange={(e) => onCheck(e.target.checked)} className="mt-1 h-4 w-4 accent-link" />
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-semibold">
            {a.title}
            {resolved && <span className="ml-2 rounded-full bg-safe-tint px-2 py-0.5 text-[10px] font-semibold text-safe">Done (simulated)</span>}
          </span>
          <span className="block text-xs text-ink-soft">{a.detail}</span>
        </span>
      </label>
      {d && text && (
        <div className="ml-7 mt-1">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            className="inline-flex h-7 items-center gap-1 text-xs font-medium text-link hover:underline"
          >
            <Icon name="mail" size={13} />
            Read the draft
            <Icon name="chevron" size={13} className={open ? "rotate-180" : ""} />
          </button>
          {open && (
            <div className="glass-inset mt-2 rounded-xl border border-line p-4">
              <div className="mb-2 flex items-center gap-1">
                {(["de", "en"] as const).map((l) => (
                  <button
                    key={l}
                    type="button"
                    aria-pressed={lang === l}
                    onClick={() => setLang(l)}
                    className={`h-6 rounded-full px-2.5 text-[11px] font-semibold uppercase ${lang === l ? "btn-primary" : "text-ink-soft hover:glass-inset"}`}
                  >
                    {l}
                  </button>
                ))}
              </div>
              <p className="text-[11px] text-ink-soft">To: {d.to}</p>
              <p className="text-[13px] font-semibold">{text.subject}</p>
              <p className="mt-2 whitespace-pre-line text-[13px]">{text.body}</p>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
