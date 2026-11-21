import { useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { useContractUpload } from "../../api/queries";
import type { ContractSimulation, LeaseCheck } from "../../api/types";
import { AutonomyBadge, ExecutionBadge } from "../../components/Badges";
import { Icon } from "../../components/Icon";
import { useToast } from "../../components/Toast";
import { formatDate, formatEur, formatEurWhole, formatLongDate } from "../../lib/format";
import { WeeklyCashChart, WeeklyChartLegend } from "../cash/CashChart";
import { btnPrimary } from "../today/DecisionCard";
import { ModelLine } from "../today/Reasoning";

/** The only upload in the app. Leases also feed Vero, the cost check. */
export function ContractPage() {
  const upload = useContractUpload();
  const [dragging, setDragging] = useState(false);

  const onFile = (file?: File) => file && upload.mutate(file);
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    onFile(e.dataTransfer.files[0]);
  };
  const demo = (name: string) => onFile(new File(["demo"], name, { type: "application/pdf" }));

  const result = upload.data;

  return (
    <div className="max-w-4xl">
      <h1 className="text-[32px] font-semibold leading-[1.1] tracking-[-0.025em] sm:text-[44px] lg:text-[48px]">Check a contract or lease</h1>
      <p className="mt-2 max-w-[66ch] text-ink-soft">
        Upload a document. Customer and supplier contracts are tested on your 13-week cash forecast. Leases are kept on
        file, so Vero can check your rent against them.
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
        <input type="file" accept=".pdf,.docx,.txt" className="sr-only" onChange={(e) => onFile(e.target.files?.[0])} />
        <span
          className={`flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-8 text-center transition-colors ${
            dragging ? "border-action bg-action-tint" : "border-line-strong"
          }`}
        >
          <Icon name="upload" size={22} className="text-ink-soft" />
          <span className="mt-2 text-base font-semibold">Choose a file or drag it here</span>
          <span className="mt-1 text-ink-soft">PDF, Word or text.</span>
        </span>
      </label>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-ink-soft">
        <span>Demo documents:</span>
        <button type="button" onClick={() => demo("Mietvertrag_Main_Street.pdf")} className="liquid-glass-pill h-8 rounded-full px-3 font-medium text-ink">
          Lease (Mietvertrag)
        </button>
        <button type="button" onClick={() => demo("Catering_Rhein-Main_Event.pdf")} className="liquid-glass-pill h-8 rounded-full px-3 font-medium text-ink">
          Summer catering contract
        </button>
        <span>A file name with “lease” or “Miet” is read as a lease.</span>
      </div>

      {upload.isPending && (
        <p role="status" className="mt-6 animate-pulse text-sm">
          Reading the document…
        </p>
      )}
      {upload.isError && <p className="mt-6 text-short">Couldn't read that file. Try a PDF or check that the backend is running.</p>}

      {result?.kind === "lease" && <LeaseResult r={result} />}
      {result?.kind === "contract" && <ContractResult sim={result} />}
    </div>
  );
}

function LeaseResult({ r }: { r: LeaseCheck }) {
  const l = r.lease;
  return (
    <section aria-labelledby="lease-h" className="mt-10 space-y-4">
      <div className="flex flex-wrap gap-2">
        <AutonomyBadge level="analysis" />
      </div>
      <h2 id="lease-h" className="text-xl font-semibold leading-snug">
        Lease read and registered: {l.landlord}
      </h2>
      <dl className="liquid-glass-strong grid overflow-hidden rounded-3xl sm:grid-cols-2">
        {[
          { k: "Monthly rent", v: formatEur(l.monthlyRentEur) },
          { k: "Since", v: formatLongDate(l.startDate) },
          { k: "Rent changes", v: l.indexClause },
          { k: "Notice", v: l.noticeRules },
        ].map((c, i) => (
          <div key={c.k} className={`border-line px-5 py-4 ${i ? "border-t" : ""} ${i === 1 ? "sm:border-t-0" : ""} ${i % 2 ? "sm:border-l" : ""}`}>
            <dt className="text-xs text-ink-soft">{c.k}</dt>
            <dd className="mt-0.5 text-[14px] font-medium">{c.v}</dd>
          </div>
        ))}
      </dl>
      <div className="liquid-glass flex flex-col gap-3 rounded-3xl p-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div>
          <p className="text-[15px] font-semibold">Vero will now check your rent against this lease.</p>
          <p className="mt-0.5 text-[13px] text-ink-soft">
            {r.costChecksTriggered > 0
              ? `It also restarted ${r.costChecksTriggered === 1 ? "1 open cost check" : `${r.costChecksTriggered} open cost checks`} that was waiting for it.`
              : "Any rent notice or invoice from this landlord is checked against §5 automatically."}
          </p>
        </div>
        <Link to={`/costs?doc=${encodeURIComponent(r.documentId)}`} className={`${btnPrimary} shrink-0`}>
          Open Cost changes
        </Link>
      </div>
      {r.model && <ModelLine model={r.model} />}
    </section>
  );
}

function ContractResult({ sim }: { sim: ContractSimulation }) {
  const toast = useToast();
  const [english, setEnglish] = useState(false);
  const t = sim.extractedTerms;
  const msg = english && sim.counterProposal.english ? sim.counterProposal.english : sim.counterProposal;

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
          <p className="font-semibold">{msg.subject}</p>
          <label htmlFor="email-body" className="sr-only">Email text</label>
          <textarea key={String(english)} id="email-body" className="glass-inset mt-3 h-56 w-full rounded-xl border border-line p-4" defaultValue={msg.body} />
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
