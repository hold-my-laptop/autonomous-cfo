import { useState } from "react";
import { Link } from "react-router-dom";
import { useCostDocuments, useDecideFinding, useDocument } from "../../api/queries";
import type { Finding, FindingStatus, RecommendedAction, TraceStep } from "../../api/types";
import { Icon } from "../../components/Icon";
import { useToast } from "../../components/Toast";
import { btnPrimary } from "../today/DecisionCard";
import { ModelLine } from "../today/Reasoning";
import { ClassificationBadge, classLabel, eur, eurDelta, PaperDoc, TOOL_ICON } from "./parts";
import { clauseName } from "./story";

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
// Static class names, so Tailwind generates them.
const COLS: Record<number, string> = { 1: "sm:grid-cols-1", 2: "sm:grid-cols-2", 3: "sm:grid-cols-3", 4: "sm:grid-cols-4", 5: "sm:grid-cols-5" };

export const isWorking = (f: Finding) => ["ingested", "extracted", "checked", "investigating"].includes(f.status);

/** One cost check: what changed → visible investigation → evidence → prepared actions → one tap. */
export function FindingView({ finding: f }: { finding: Finding }) {
  const statusIdx = PIPELINE.findIndex((p) => p.status === f.status);
  const li = f.line_item;
  const working = isWorking(f);
  const sign = li.variance_pct > 0 ? "+" : "";

  return (
    <div className="space-y-4">
      <section aria-labelledby={`inv-h-${f.finding_id}`} className="liquid-glass-strong rounded-3xl p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-xs text-ink-soft">
              <Icon name="mail" size={13} />
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

        <dl className="glass-inset mt-4 grid grid-cols-3 rounded-xl">
          {[
            { k: li.unit === "month" ? "Paid until now" : "Expected", v: eur(li.previous_value) },
            { k: li.unit === "month" ? "Asked for" : "Billed", v: eur(li.current_value), bad: true },
            { k: "Change", v: `${eurDelta(li.monthly_variance)} · ${sign}${li.variance_pct.toLocaleString("en-GB")}%`, bad: true },
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
        {f.reason && !working && <p className="mt-3 text-[13px] text-ink-soft">{f.reason}</p>}
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
        id={f.finding_id}
        steps={f.investigation_trace}
        running={f.status === "investigating"}
        visible={statusIdx >= 3 || f.investigation_trace.length > 0}
      />

      {!working && <Outcome key={f.finding_id} finding={f} />}
    </div>
  );
}

function Trace({ id, steps, running, visible }: { id: string; steps: TraceStep[]; running: boolean; visible: boolean }) {
  if (!visible) return null;
  const sorted = [...steps].sort((a, b) => a.step - b.step);
  return (
    <section aria-labelledby={`trace-h-${id}`} className="liquid-glass-strong rounded-3xl p-5 sm:p-7">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={`trace-h-${id}`} className="text-[20px] font-semibold">How Vero found out</h2>
        <p className="text-[11px] text-ink-soft">
          {sorted.length} of max {MAX_TOOL_CALLS} tool calls · read-only tools · maths by the deterministic engine
        </p>
      </div>
      {sorted.length === 0 && !running && <p className="mt-3 text-[13px] text-ink-soft">No look-ups were needed: the evidence on file explained it.</p>}
      <ol className="mt-5 space-y-2" aria-live="polite">
        {sorted.map((s) => (
          <li key={s.step} className="glass-inset flex gap-4 rounded-xl p-3 sm:p-4">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-navy text-sand">
              <Icon name={s.tool ? TOOL_ICON[s.tool] : "search"} size={15} />
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <p className="text-[13px] font-semibold">
                {s.step}. {s.hypothesis || s.call}
              </p>
              {s.hypothesis && <p className="mt-1 break-words text-[12px] text-ask">{s.call}</p>}
              {s.result && <p className="mt-1.5 text-[13px] text-ink-soft">{s.result}</p>}
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
  const decide = useDecideFinding();
  const { data: docs } = useCostDocuments();
  const v = finding.verdict;
  const resolved = finding.status === "resolved";
  const tone = finding.classification === "conflict" ? "border-short/25 bg-short-tint" : finding.classification === "verified" ? "border-safe/25 bg-safe-tint" : "border-ask/25 bg-cream";

  // Only evidence with a document opens side by side; emails and reference values are listed by id.
  const openable = finding.evidence_refs.filter((e) => e.doc_id && e.doc_id !== finding.source_doc_id);
  const listed = finding.evidence_refs.filter((e) => !e.doc_id);
  const docLabel = (id: string, fallback: string) => {
    const known = docs?.find((d) => d.doc_id === id)?.label;
    return known ?? fallback;
  };
  const [right, setRight] = useState(openable.find((e) => e.type === "lease")?.doc_id ?? openable[0]?.doc_id ?? null);
  const [english, setEnglish] = useState(true);
  const left = useDocument(finding.source_doc_id);
  const rightDoc = useDocument(right);
  const rightRef = openable.find((e) => e.doc_id === right);

  const actions = finding.recommended_actions;
  const pending = actions.filter((a) => a.status === "pending");
  const [selected, setSelected] = useState<Record<string, boolean>>(() => Object.fromEntries(pending.map((a) => [a.id, true])));
  const approve = pending.filter((a) => selected[a.id]).map((a) => a.id);
  const reject = pending.filter((a) => !selected[a.id]).map((a) => a.id);
  const buttonLabel =
    approve.length === 0
      ? `Decline ${reject.length === 1 ? "it" : `all ${reject.length}`}`
      : reject.length === 0
        ? `Approve ${approve.length === 1 ? "it" : `all ${approve.length}`}`
        : `Approve ${approve.length}, decline ${reject.length}`;

  return (
    <>
      {v && (
        <section aria-labelledby={`verdict-h-${finding.finding_id}`} className={`rounded-3xl border p-5 sm:p-6 ${tone}`}>
          <h2 id={`verdict-h-${finding.finding_id}`} className="text-[20px] font-semibold leading-snug">
            {v.title}
          </h2>
          {v.detail && <p className="mt-1 text-[13px] text-ink-soft">{v.detail}</p>}
          <dl className={`mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-line ${COLS[Math.min(v.cells.length, 5)]}`}>
            {v.cells.map((c) => (
              <div key={c.k} className="bg-surface px-4 py-3">
                <dt className="text-xs text-ink-soft">{c.k}</dt>
                <dd className={`num tight mt-0.5 text-lg font-semibold ${c.tone === "bad" ? "text-short" : c.tone === "good" ? "text-safe" : ""}`}>{c.v}</dd>
                <dd className="font-mono text-[10.5px] text-ink-muted">{c.calc}</dd>
              </div>
            ))}
          </dl>
          {v.note && <p className="mt-2 text-[11px] text-ink-muted">{v.note}</p>}
        </section>
      )}

      {(finding.source_doc_id || openable.length > 0) && (
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
                  {d.item_id ? `${docLabel(d.doc_id!, d.label)} ${clauseName(d.item_id)}` : docLabel(d.doc_id!, d.label)}
                </button>
              ))}
              <label className="ml-2 inline-flex items-center gap-1.5 text-xs text-ink-soft">
                <input type="checkbox" checked={english} onChange={(e) => setEnglish(e.target.checked)} className="accent-link" />
                English
              </label>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {finding.source_doc_id &&
              (left.data ? <PaperDoc doc={left.data} showEnglish={english} /> : left.isError ? <DocMissing id={finding.source_doc_id} /> : <div className="h-64 animate-pulse rounded-3xl bg-sunk" />)}
            {right &&
              (rightDoc.data ? (
                <PaperDoc doc={rightDoc.data} showEnglish={english} focus={rightRef?.item_id} focusLabel={rightRef?.item_id ? `${clauseName(rightRef.item_id)} cited` : undefined} />
              ) : rightDoc.isError ? (
                <DocMissing id={right} />
              ) : (
                <div className="h-64 animate-pulse rounded-3xl bg-sunk" />
              ))}
          </div>
          {listed.length > 0 && (
            <p className="mt-3 flex flex-wrap items-center gap-1.5 text-xs text-ink-soft">
              Also cited:
              {listed.map((e) => (
                <span key={`${e.type}-${e.ref}`} className="rounded-full bg-sunk px-2 py-0.5">
                  {e.label}
                  {e.ref && <span className="ml-1 font-mono text-[10.5px] text-ink-muted">{e.ref}</span>}
                </span>
              ))}
            </p>
          )}
        </section>
      )}

      {finding.propagation.length > 0 && (
        <section aria-labelledby={`prop-h-${finding.finding_id}`}>
          <h2 id={`prop-h-${finding.finding_id}`} className="mb-3 text-[20px] font-semibold">What changes downstream</h2>
          <div className="liquid-glass-strong overflow-x-auto rounded-3xl">
            <table className="w-full min-w-[520px] text-[13px]">
              <thead className="glass-inset border-b border-line text-left text-xs text-ink-soft">
                <tr>
                  <th scope="col" className="px-4 py-2 font-medium sm:px-5">Where</th>
                  <th scope="col" className="px-4 py-2 font-medium">Note</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium sm:pr-5">Change</th>
                </tr>
              </thead>
              <tbody className="num">
                {finding.propagation.map((p) => (
                  <tr key={p.id} className="border-t border-line first:border-t-0">
                    <td className="px-4 py-3 font-medium sm:px-5">{p.where}</td>
                    <td className="px-4 py-3 text-ink-soft">{p.note}</td>
                    <td className="px-4 py-3 text-right sm:pr-5">{p.change === 0 ? "None" : eurDelta(p.change)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {actions.length > 0 && (
        <section aria-labelledby={`act-h-${finding.finding_id}`} className="liquid-glass-strong rounded-3xl p-5 sm:p-6">
          <p className="flex items-center gap-1.5 text-[11px] font-semibold text-ask">
            <span className="h-2 w-2 rounded-full bg-sand" aria-hidden="true" />
            {pending.length ? "Needs your OK" : resolved ? "Decided · simulated" : "Decided"}
          </p>
          <h2 id={`act-h-${finding.finding_id}`} className="mt-2 text-[20px] font-semibold">Prepared actions</h2>
          <ul className="glass-inset mt-3 divide-y divide-sand/40 rounded-xl">
            {actions.map((a) => (
              <ActionRow key={a.id} a={a} checked={!!selected[a.id]} onCheck={(on) => setSelected((s) => ({ ...s, [a.id]: on }))} />
            ))}
          </ul>
          {pending.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={decide.isPending}
                onClick={async () => {
                  try {
                    await decide.mutateAsync({ id: finding.finding_id, approve, reject });
                    toast(
                      `${approve.length ? `Approved ${approve.length}` : ""}${approve.length && reject.length ? ", " : ""}${reject.length ? `declined ${reject.length}` : ""} (simulated). Nothing was sent to the bank or to ${finding.vendor}.`,
                    );
                  } catch {
                    toast("That didn't go through. Check the connection and try again.", "neutral");
                  }
                }}
                className={btnPrimary}
              >
                {decide.isPending ? "Working…" : buttonLabel}
              </button>
              <span className="self-center text-xs text-ink-soft">Untick what you don't want. Simulated: nothing is sent.</span>
            </div>
          )}
        </section>
      )}
    </>
  );
}

function DocMissing({ id }: { id: string }) {
  return (
    <div className="flex h-64 items-center justify-center rounded-3xl border border-dashed border-line-strong p-6 text-center text-[13px] text-ink-soft">
      Couldn't open {id}.
    </div>
  );
}

const STATUS_TAG: Record<RecommendedAction["status"], { label: string; cls: string } | null> = {
  pending: null,
  approved: { label: "Approved (simulated)", cls: "bg-safe-tint text-safe" },
  rejected: { label: "Declined", cls: "bg-sunk text-ink-soft" },
  other: { label: "No longer needed", cls: "bg-sunk text-ink-soft" },
};

function ActionRow({ a, checked, onCheck }: { a: RecommendedAction; checked: boolean; onCheck: (v: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const [lang, setLang] = useState<"de" | "en">("de");
  const d = a.draft;
  const text = d && lang === "en" && d.english ? d.english : d;
  const tag = STATUS_TAG[a.status];
  const decided = a.status !== "pending";
  return (
    <li className="px-4 py-3">
      <label className="flex items-start gap-3">
        <input type="checkbox" disabled={decided} checked={decided ? a.status === "approved" : checked} onChange={(e) => onCheck(e.target.checked)} className="mt-1 h-4 w-4 accent-link" />
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-semibold">
            {a.title}
            {tag && <span className={`ml-2 rounded-full px-2 py-0.5 text-[10px] font-semibold ${tag.cls}`}>{tag.label}</span>}
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
            Read the letter
            <Icon name="chevron" size={13} className={open ? "rotate-180" : ""} />
          </button>
          {open && (
            <div className="glass-inset mt-2 rounded-xl border border-line p-4">
              <div className="mb-2 flex flex-wrap items-center gap-1">
                {d.english ? (
                  (["de", "en"] as const).map((l) => (
                    <button
                      key={l}
                      type="button"
                      aria-pressed={lang === l}
                      onClick={() => setLang(l)}
                      className={`h-6 rounded-full px-2.5 text-[11px] font-semibold uppercase ${lang === l ? "btn-primary" : "text-ink-soft hover:glass-inset"}`}
                    >
                      {l}
                    </button>
                  ))
                ) : (
                  <span className="text-[11px] text-ink-soft">In German. There is no English version.</span>
                )}
                {d.source && (
                  <span className="ml-auto text-[11px] text-ink-muted">
                    {d.source === "MODEL" ? "Drafted by the model, checked by Vero" : "Vero's template, from the computed amounts"}
                  </span>
                )}
              </div>
              <p className="text-[11px] text-ink-soft">To: {d.to}</p>
              <div lang={lang === "en" && d.english ? "en" : (d.language ?? "de")}>
                <p className="text-[13px] font-semibold">{text.subject}</p>
                <p className="mt-2 whitespace-pre-line text-[13px]">{text.body}</p>
              </div>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

