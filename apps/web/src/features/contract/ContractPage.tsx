import { useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, MAX_UPLOAD_BYTES, USE_MOCK } from "../../api/client";
import { useContractSimulation, useFindings, useUploadBill } from "../../api/queries";
import type { ContractSimulation, UploadRun } from "../../api/types";
import { AutonomyBadge, ExecutionBadge } from "../../components/Badges";
import { Icon } from "../../components/Icon";
import { useToast } from "../../components/Toast";
import { formatDate, formatEurWhole } from "../../lib/format";
import { WeeklyCashChart, WeeklyChartLegend } from "../cash/CashChart";
import { btnPrimary } from "../today/DecisionCard";
import { ModelLine } from "../today/Reasoning";
import { ClassificationBadge } from "../vero/parts";

/** The demo document of PROOF §7.1, served from public/demo (byte-identical to the backend's fixture). */
const DEMO_FILE = "doc_rent_adjustment_2025_06.pdf";

/** What the owner reads when an upload is refused (POST /documents error codes). */
function uploadError(err: unknown): { text: string; toCosts?: boolean } {
  if (err instanceof ApiError) {
    if (err.code === "RUN_IN_PROGRESS") return { text: "Vero is still checking something else. Try again in a moment." };
    if (err.code === "DOCUMENT_ALREADY_PROCESSED")
      return { text: "Vero already has this document. Its check is on Cost check.", toCosts: true };
    if (err.code === "FILE_TOO_LARGE" || err.status === 413) return { text: "That file is larger than 5 MB. Try a smaller PDF." };
    if (err.status === 422) return { text: "That isn't a PDF Vero can read. Upload the bill or notice as a PDF." };
  }
  if (err instanceof Error && !(err instanceof ApiError)) return { text: err.message };
  return { text: "Couldn't upload that file. Check that the backend is running." };
}

/**
 * Upload a bill or notice: Vero reads it, checks it against Cost Memory and, if something changed
 * without a reason on file, investigates. Only bills are checked (the rent adjustment, invoices);
 * leases and contracts are already loaded, so uploading them again gives "already processed".
 */
export function ContractPage() {
  const [progress, setProgress] = useState<UploadRun | null>(null);
  const upload = useUploadBill(setProgress);
  const [dragging, setDragging] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [fetchingDemo, setFetchingDemo] = useState(false);

  const onFile = (file?: File) => {
    if (!file) return;
    setRefused(null);
    setProgress(null);
    if (file.size > MAX_UPLOAD_BYTES) return setRefused("That file is larger than 5 MB. Try a smaller PDF.");
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") return setRefused("Only PDF files can be checked.");
    upload.mutate(file);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    onFile(e.dataTransfer.files[0]);
  };
  const demo = async () => {
    if (USE_MOCK) return onFile(new File(["demo"], DEMO_FILE, { type: "application/pdf" }));
    setFetchingDemo(true);
    try {
      const res = await fetch(`${import.meta.env.BASE_URL}demo/${DEMO_FILE}`);
      if (!res.ok) throw new Error();
      onFile(new File([await res.blob()], DEMO_FILE, { type: "application/pdf" }));
    } catch {
      setRefused("Couldn't load the demo document.");
    } finally {
      setFetchingDemo(false);
    }
  };

  const busy = upload.isPending || fetchingDemo;
  const error = upload.isError ? uploadError(upload.error) : null;
  const done = upload.data;

  return (
    <div className="max-w-4xl">
      <h1 className="text-[32px] font-semibold leading-[1.1] tracking-[-0.025em] sm:text-[44px] lg:text-[48px]">Upload a bill or notice</h1>
      <p className="mt-2 max-w-[66ch] text-ink-soft">
        Vero reads it, checks it against what you usually pay, your contracts and your price lists, and finds out why
        anything changed. Your lease, contracts and price lists are already on file.
      </p>

      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        aria-disabled={busy}
        className={`liquid-glass liquid-hover mt-6 block rounded-3xl p-2 focus-within:outline-3 focus-within:outline-action ${busy ? "pointer-events-none opacity-60" : "cursor-pointer"}`}
      >
        <input type="file" accept=".pdf,application/pdf" disabled={busy} className="sr-only" onChange={(e) => onFile(e.target.files?.[0])} />
        <span
          className={`flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-8 text-center transition-colors ${
            dragging ? "border-action bg-action-tint" : "border-line-strong"
          }`}
        >
          <Icon name="upload" size={22} className="text-ink-soft" />
          <span className="mt-2 text-base font-semibold">Choose a PDF or drag it here</span>
          <span className="mt-1 text-ink-soft">A bill, invoice or rent adjustment. PDF, up to 5 MB.</span>
        </span>
      </label>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-ink-soft">
        <span>Demo document:</span>
        <button type="button" disabled={busy} onClick={demo} className="liquid-glass-pill h-8 rounded-full px-3 font-medium text-ink disabled:opacity-50">
          Rent adjustment from the landlord
        </button>
        <span className="font-mono text-[11px] text-ink-muted">{DEMO_FILE}</span>
        {USE_MOCK && <span>Demo mode reads every upload as this rent adjustment.</span>}
      </div>

      <div aria-live="polite">
        {busy && (
          <p role="status" className="mt-6 flex items-center gap-2 text-sm">
            <span className="h-2 w-2 animate-pulse rounded-full bg-ask" aria-hidden="true" />
            {progress?.latest ?? "Uploading…"}
          </p>
        )}
        {refused && <p role="alert" className="mt-6 text-short">{refused}</p>}
        {error && (
          <p role="alert" className="mt-6 text-short">
            {error.text}{" "}
            {error.toCosts && (
              <Link to="/costs" className="font-medium text-link underline">
                Open Cost check
              </Link>
            )}
          </p>
        )}
        {done && <UploadResult run={done} />}
      </div>

      {USE_MOCK && <ContractSimulationSection />}
    </div>
  );
}

/** What the check found, with a way to the full card on Cost check. */
function UploadResult({ run }: { run: UploadRun }) {
  const { data: findings } = useFindings();
  const found = (findings ?? []).filter((f) => run.finding_ids.includes(f.finding_id));
  if (run.status === "failed") {
    return (
      <section className="liquid-glass-strong mt-6 rounded-3xl p-5 sm:px-6">
        <p className="text-[15px] font-semibold">Vero couldn't finish checking this document.</p>
        {run.headline && <p className="mt-1 text-[13px] text-ink-soft">{run.headline}</p>}
      </section>
    );
  }
  return (
    <section aria-labelledby="upload-h" className="liquid-glass-strong mt-6 space-y-3 rounded-3xl p-5 sm:p-6">
      <h2 id="upload-h" className="text-[18px] font-semibold leading-snug">
        {found.length ? "Checked." : run.finding_ids.length ? "Checked. Loading what Vero found…" : "Checked: it matches what you expect."}
      </h2>
      {run.headline && <p className="text-[13px] text-ink-soft">{run.headline}</p>}
      {found.length > 0 && (
        <ul className="space-y-2">
          {found.map((f) => (
            <li key={f.finding_id} className="glass-inset flex flex-wrap items-center justify-between gap-2 rounded-xl px-4 py-3">
              <span className="text-[13px] font-semibold">{f.title}</span>
              <ClassificationBadge value={f.classification} />
            </li>
          ))}
        </ul>
      )}
      <Link to="/costs" className={`${btnPrimary} inline-flex`}>
        See the check on Cost check
      </Link>
    </section>
  );
}

/** Mock only: a customer contract tested on the 13-week forecast. */
function ContractSimulationSection() {
  const sim = useContractSimulation();
  return (
    <section aria-labelledby="sim-h" className="mt-14 border-t border-line pt-8">
      <h2 id="sim-h" className="text-[20px] font-semibold">Test a customer contract</h2>
      <p className="mt-1 max-w-[66ch] text-[13px] text-ink-soft">
        Demo mode only: see what a catering contract does to your 13-week cash forecast before you sign.
      </p>
      <button
        type="button"
        disabled={sim.isPending}
        onClick={() => sim.mutate(new File(["demo"], "Catering_Rhein-Main_Event.pdf", { type: "application/pdf" }))}
        className="liquid-glass-pill mt-3 h-9 rounded-full px-4 text-[13px] font-semibold disabled:opacity-50"
      >
        {sim.isPending ? "Testing it on your forecast…" : "Test the summer catering contract"}
      </button>
      {sim.isError && <p role="alert" className="mt-4 text-short">Couldn't test that contract.</p>}
      {sim.data && <ContractResult sim={sim.data} />}
    </section>
  );
}

function ContractResult({ sim }: { sim: ContractSimulation }) {
  const toast = useToast();
  const [english, setEnglish] = useState(false);
  const t = sim.extractedTerms;
  const msg = english && sim.counterProposal.english ? sim.counterProposal.english : sim.counterProposal;
  // Screen readers switch pronunciation by lang (the original counter-proposal is German).
  const msgLang =
    english && sim.counterProposal.english ? "en" : (sim.counterProposal.language ?? (sim.counterProposal.english ? "de" : "en"));

  return (
    <div className="mt-10 space-y-10">
      <section aria-labelledby="verdict-h">
        <div className="flex flex-wrap gap-2">
          <AutonomyBadge level="recommendation" />
          <ExecutionBadge mode="simulated" />
        </div>
        <h2 id="verdict-h" className="mt-3 max-w-[44ch] text-xl font-semibold leading-snug text-short">
          As written, this contract makes your June overdraft {formatEurWhole(sim.extraOverdraftEur)} deeper.
        </h2>
        <p className="mt-2 max-w-[70ch] text-sm">
          {t.counterparty} would pay {formatEurWhole(t.valueEur)} for the event on {formatDate(t.startDate)}, but only{" "}
          {t.paymentTermDays} days later and with {t.advancePct}% up front. You'd pay {formatEurWhole(t.costEur)} for food and
          staff first, so your lowest point becomes {formatEurWhole(sim.minBalanceEur)} in W{sim.valleyWeek}.
        </p>
        <div className="liquid-glass liquid-navy mt-4 space-y-3 rounded-3xl p-5 text-on-hero sm:p-6">
          <WeeklyChartLegend compareLabel="Without this contract" />
          <WeeklyCashChart
            weeks={sim.withContract}
            compareWeeks={sim.baseline}
            compareLabel="Without this contract"
            caption="Bars: your cash with the contract as written. White line: without the contract."
          />
        </div>
      </section>

      <section aria-labelledby="fix-h">
        <h2 id="fix-h" className="text-lg font-semibold tracking-tight">How to make it safe</h2>
        <p className="mt-2 max-w-[70ch] text-sm">{sim.suggestion}</p>
        <div className="liquid-glass liquid-navy mt-4 space-y-3 rounded-3xl p-5 text-on-hero sm:p-6">
          <WeeklyChartLegend compareLabel="As written" />
          <WeeklyCashChart
            weeks={sim.withSuggestion}
            compareWeeks={sim.withContract}
            compareLabel="As written"
            height={280}
            caption="Bars: your cash with the safer terms. White line: the contract as written."
          />
        </div>
      </section>

      <section aria-labelledby="email-h">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 id="email-h" className="text-lg font-semibold tracking-tight">Email to {t.counterparty}</h2>
            <p className="mt-1 text-ink-soft">I wrote this for you in German. Change anything you like, then copy it.</p>
          </div>
          {sim.counterProposal.english && (
            <label className="inline-flex items-center gap-1.5 text-xs text-ink-soft">
              <input type="checkbox" checked={english} onChange={(e) => setEnglish(e.target.checked)} className="accent-link" />
              Show in English
            </label>
          )}
        </div>
        <div className="liquid-glass-strong mt-4 rounded-3xl p-5">
          <p className="text-sm text-ink-soft">To: {sim.counterProposal.to}</p>
          <p className="font-semibold" lang={msgLang}>{msg.subject}</p>
          <label htmlFor="email-body" className="sr-only">Email text</label>
          <textarea key={String(english)} id="email-body" lang={msgLang} className="glass-inset mt-3 h-56 w-full rounded-xl border border-line p-4" defaultValue={msg.body} />
          <button
            className="btn-primary mt-4 h-10 rounded-full px-5 text-[14px] font-semibold"
            onClick={async () => {
              const text = (document.getElementById("email-body") as HTMLTextAreaElement).value;
              try {
                await navigator.clipboard.writeText(text);
                toast("Email copied. Paste it into your mail program.");
              } catch {
                toast("Couldn't copy automatically. Select the text and copy it by hand.", "neutral");
              }
            }}
          >
            Copy email
          </button>
        </div>
        {sim.model && <ModelLine model={sim.model} className="mt-3" />}
      </section>
    </div>
  );
}
