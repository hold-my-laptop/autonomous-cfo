import { useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, USE_MOCK } from "../../api/client";
import { useContractUpload, useDocumentRun, useUploadDocument } from "../../api/queries";
import type { ContractSimulation } from "../../api/types";
import { AutonomyBadge, ExecutionBadge } from "../../components/Badges";
import { Icon } from "../../components/Icon";
import { useToast } from "../../components/Toast";
import { formatDate, formatEurWhole } from "../../lib/format";
import { WeeklyCashChart, WeeklyChartLegend } from "../cash/CashChart";
import { btnPrimary } from "../today/DecisionCard";
import { ModelLine } from "../today/Reasoning";

const MAX_BYTES = 5 * 1024 * 1024;
/** The demo bill: the landlord's rent adjustment (docs/PROOF.md §7.1). Leases and contracts are already on file. */
const DEMO_FILE = "doc_rent_adjustment_2025_06.pdf";

/** What an upload error means for the owner (POST /documents: 409, 413, 422). */
function uploadError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "DOCUMENT_ALREADY_PROCESSED")
      return "Vero already has this document. Its check is on Cost changes. To run the demo again, press Start again on the presenter panel (?demo=1) first.";
    if (e.code === "RUN_IN_PROGRESS") return "Vero is still working on another check. Try again in a moment.";
    if (e.code === "FILE_TOO_LARGE" || e.status === 413) return "That file is larger than 5 MB.";
    if (e.status === 422) return "That isn't a PDF Vero can read. Upload the bill or notice as a PDF.";
  }
  if (e instanceof Error && e.message && !(e instanceof ApiError)) return e.message;
  return "Couldn't upload that file. Check that the backend is running and try again.";
}

/** Why reading an upload failed (the run's FALLBACK reason), and what the owner can do. */
const READ_FAILED: Record<string, string> = {
  AGENT_DISABLED:
    "Reading with the AI model is off on this backend, so Vero can only read the demo documents. Upload the demo rent adjustment, or start the AI service to read your own bills.",
  AI_UNREACHABLE: "The AI service didn't answer. Try again in a moment.",
  DEADLINE_PASSED: "Reading took too long. Try again in a moment.",
  UNREADABLE_DOCUMENT: "Vero couldn't read the text of this PDF. A scan or a protected PDF can't be read.",
  INVALID_ANSWER: "What Vero read from it didn't pass its checks, so nothing was changed.",
  SERVICE_RESTARTED: "Vero restarted while reading it. Upload it again.",
};

/** Upload a bill or notice: Vero reads it, checks it against your contracts, and shows what it found. */
export function ContractPage() {
  const upload = useUploadDocument();
  const run = useDocumentRun(upload.data?.run_id ?? null);
  const [dragging, setDragging] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const onFile = (file?: File) => {
    if (!file) return;
    setProblem(null);
    if (file.size > MAX_BYTES) return setProblem("That file is larger than 5 MB.");
    if (file.type && file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) return setProblem("Only PDFs can be checked.");
    upload.mutate(file);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    onFile(e.dataTransfer.files[0]);
  };
  const demo = async () => {
    setProblem(null);
    try {
      const res = await fetch(`${import.meta.env.BASE_URL}demo/${DEMO_FILE}`);
      if (!res.ok) throw new Error();
      onFile(new File([await res.blob()], DEMO_FILE, { type: "application/pdf" }));
    } catch {
      setProblem("Couldn't load the demo file.");
    }
  };

  const r = run.data;
  const working = upload.isPending || (!!upload.data && (!r || r.status === "RUNNING"));
  const error = problem ?? (upload.isError ? uploadError(upload.error) : run.isError ? uploadError(run.error) : null);

  return (
    <div className="max-w-4xl">
      <h1 className="text-[32px] font-semibold leading-[1.1] tracking-[-0.025em] sm:text-[44px] lg:text-[48px]">Upload a bill or notice</h1>
      <p className="mt-2 max-w-[66ch] text-ink-soft">
        Vero reads it, compares it with what you usually pay and with your contracts, and looks for the reason when
        something changed. Your lease and contracts are already on file. Bills usually arrive by themselves; use this
        when one came by post.
      </p>

      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className="liquid-glass liquid-hover mt-6 block cursor-pointer rounded-3xl p-2 focus-within:outline-3 focus-within:outline-action"
      >
        <input
          type="file"
          accept=".pdf,application/pdf"
          className="sr-only"
          disabled={working}
          onChange={(e) => {
            onFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        <span
          className={`flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-8 text-center transition-colors ${
            dragging ? "border-action bg-action-tint" : "border-line-strong"
          }`}
        >
          <Icon name="upload" size={22} className="text-ink-soft" />
          <span className="mt-2 text-base font-semibold">Choose a PDF or drag it here</span>
          <span className="mt-1 text-ink-soft">A bill, invoice or notice. PDF, at most 5 MB.</span>
          <span className="mt-1 text-xs text-ink-muted">
            Checked: invoices and rent notices from suppliers and the landlord Vero knows, dated up to today. Contracts and leases aren't checked.
          </span>
        </span>
      </label>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-ink-soft">
        <span>Demo document:</span>
        <button type="button" disabled={working} onClick={demo} className="liquid-glass-pill h-8 rounded-full px-3 font-medium text-ink">
          Rent adjustment from the landlord
        </button>
        <span className="font-mono text-[11px] text-ink-muted">{DEMO_FILE}</span>
      </div>

      {error && (
        <p role="alert" className="mt-6 text-short">
          {error}
          {/already has this document/.test(error) && (
            <Link to="/costs" className="ml-2 font-medium text-link hover:underline">
              Open Cost changes
            </Link>
          )}
        </p>
      )}

      {upload.data && <RunProgress steps={r?.steps ?? []} working={working} />}

      {r && r.status === "SUCCEEDED" && (
        <div className="liquid-glass mt-4 flex flex-col gap-3 rounded-3xl p-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <div>
            <p className="text-[15px] font-semibold">{r.headline ?? "Checked."}</p>
            <p className="mt-0.5 text-[13px] text-ink-soft">
              {r.finding_ids.length
                ? "Open Cost changes for the evidence, what changes downstream and the prepared actions."
                : r.checked
                  ? "Nothing differs from what you expect."
                  : "Vero kept the document but didn't check it: the sender isn't a supplier or landlord it knows."}
            </p>
          </div>
          <Link to={`/costs?doc=${encodeURIComponent(r.document_id)}`} className={`${btnPrimary} shrink-0`}>
            Open Cost changes
          </Link>
        </div>
      )}
      {r && r.status === "FAILED" && (
        <div role="alert" className="mt-4 rounded-3xl border border-short/25 bg-short-tint p-5 sm:px-6">
          <p className="text-[15px] font-semibold">{r.headline ?? "Vero couldn't read this document."}</p>
          <p className="mt-1 text-[13px]">{(r.reason && READ_FAILED[r.reason]) ?? "Nothing was changed. Try again, or upload the demo rent adjustment."}</p>
          {r.detail && r.reason !== "AGENT_DISABLED" && <p className="mt-1 text-[12px] text-ink-soft">Details: {r.detail}</p>}
        </div>
      )}

      {USE_MOCK && <ContractSimulator />}
    </div>
  );
}

function RunProgress({ steps, working }: { steps: string[]; working: boolean }) {
  return (
    <section aria-labelledby="run-h" className="liquid-glass-strong mt-6 rounded-3xl p-5 sm:p-6">
      <h2 id="run-h" className="flex items-center gap-2 text-[15px] font-semibold">
        {working ? <span className="h-2 w-2 animate-pulse rounded-full bg-ask" aria-hidden="true" /> : <Icon name="check" size={14} className="text-safe" />}
        {working ? "Vero is checking it…" : "What Vero did"}
      </h2>
      <ol className="mt-3 space-y-1 text-[13px] text-ink-soft" aria-live="polite">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2">
            <span className="num w-5 shrink-0 text-right text-ink-muted">{i + 1}.</span>
            <span>{s}</span>
          </li>
        ))}
        {working && steps.length === 0 && <li>Uploading…</li>}
      </ol>
    </section>
  );
}

/** Mock only: a customer or supplier contract, tested on the 13-week forecast. */
function ContractSimulator() {
  const upload = useContractUpload();
  const result = upload.data;
  return (
    <section aria-labelledby="sim-h" className="mt-14 border-t border-line pt-8">
      <h2 id="sim-h" className="text-[20px] font-semibold">Test a contract on your forecast</h2>
      <p className="mt-1 max-w-[66ch] text-[13px] text-ink-soft">Demo only: the real backend checks bills, not contracts.</p>
      <button
        type="button"
        onClick={() => upload.mutate(new File(["demo"], "Catering_Rhein-Main_Event.pdf", { type: "application/pdf" }))}
        className="liquid-glass-pill mt-3 h-8 rounded-full px-3 text-xs font-medium text-ink"
      >
        Summer catering contract
      </button>
      {upload.isPending && (
        <p role="status" className="mt-6 animate-pulse text-sm">
          Reading the contract…
        </p>
      )}
      {upload.isError && <p role="alert" className="mt-6 text-short">Couldn't read that contract.</p>}
      {result && <ContractResult sim={result} />}
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
