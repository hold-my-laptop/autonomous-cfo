import { Link, useSearchParams } from "react-router-dom";
import { useCompany, useCostDocuments, useCostMemory, useDigest, useFindings } from "../../api/queries";
import type { Finding, KnownDocument } from "../../api/types";
import { Icon } from "../../components/Icon";
import { FindingView, isWorking } from "./Investigation";
import { ClassificationBadge, eur } from "./parts";

const monthShort = (period: string) => new Date(`${period}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });

const cites = (f: Finding, docId: string) => f.source_doc_id === docId || f.evidence_refs.some((e) => e.doc_id === docId);

function headline(findings: Finding[]) {
  const open = findings.filter((f) => f.status !== "resolved");
  const needsOk = open.filter((f) => f.status === "pending_approval");
  if (needsOk.length) {
    const atStake = needsOk.reduce((s, f) => s + (f.financial_impact?.disputed_amount_annual ?? 0), 0);
    return `${needsOk.length === 1 ? "One cost change needs" : `${needsOk.length} cost changes need`} you. ${eur(atStake)} a year is at stake.`;
  }
  if (open.some((f) => f.evidence_request)) return "Vero needs one document from you to finish a check.";
  if (open.some(isWorking)) return "A cost changed without a reason on file. Vero is looking into it.";
  if (findings.length) return "All cost changes are handled.";
  return "Nothing needs you. Every recurring cost matches what you expect.";
}

/** Vero: finds out why a recurring cost changed, proves what to pay, fixes what depends on it. */
export function VeroPage() {
  const [params, setParams] = useSearchParams();
  const docFilter = params.get("doc");
  const { data: company } = useCompany();
  const findings = useFindings();
  const docs = useCostDocuments();
  const digest = useDigest();

  const all = findings.data ?? [];
  const shown = docFilter ? all.filter((f) => cites(f, docFilter)) : all;
  const needsYou = all.filter((f) => f.status === "pending_approval" || (f.evidence_request && f.status !== "resolved")).length;
  const protectedEur = all
    .filter((f) => f.classification === "conflict")
    .reduce((s, f) => s + (f.financial_impact?.disputed_amount_annual ?? 0), 0);
  const quiet = digest.data?.length ?? 0;
  const filterDoc = docs.data?.find((d) => d.doc_id === docFilter);

  return (
    <div className="space-y-10">
      <section aria-labelledby="vero-h">
        <p className="flex flex-wrap items-center gap-2 text-xs text-ink-soft">
          <span className="rounded-full bg-sunk px-2 py-0.5 font-semibold text-ink">Vero · Cost check</span>
          {company ? `${company.name}, ${company.city}` : " "}
        </p>
        <h1 id="vero-h" className="mt-2 max-w-[30ch] text-[32px] font-semibold leading-[1.1] tracking-[-0.025em] sm:text-[44px] lg:text-[48px]">
          {findings.data ? headline(all) : "Checking your costs…"}
        </h1>
        <p className="mt-2 max-w-[80ch] text-[15px] text-ink-soft">
          Vero checks every recurring cost against your bank history, contracts and leases. Bills reach it by themselves:
          bank feed, e-mail and e-invoices. When something changes without a reason on file, it finds the reason, proves
          what you should pay, and prepares the changes for your OK.
        </p>
      </section>

      <dl className="liquid-glass grid grid-cols-2 rounded-3xl lg:grid-cols-4">
        {[
          { k: "Changes checked", v: String(quiet + all.length), sub: "This month" },
          { k: "Handled quietly", v: String(quiet), sub: "Explained by the evidence, no alert" },
          { k: "Needs you", v: String(needsYou), sub: needsYou ? "See below" : "Nothing waiting", alert: needsYou > 0 },
          { k: "Protected", v: eur(protectedEur), sub: "Disputed amount, per year" },
        ].map((c, i) => (
          <div
            key={c.k}
            className={`border-line px-5 py-4 sm:px-6 ${i % 2 ? "border-l" : ""} ${i > 1 ? "border-t lg:border-t-0" : ""} ${i === 2 ? "lg:border-l" : ""}`}
          >
            <dt className="text-xs text-ink-soft">{c.k}</dt>
            <dd className={`num tight mt-1 text-2xl font-semibold ${c.alert ? "text-short" : ""}`}>{c.v}</dd>
            <dd className="mt-0.5 text-xs text-ink-soft">{c.sub}</dd>
          </div>
        ))}
      </dl>

      <KnownDocuments docs={docs.data ?? []} highlight={docFilter} />

      <section aria-labelledby="checks-h" className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 id="checks-h" className="text-[20px] font-semibold">Cost checks</h2>
            {docFilter && (
              <p className="text-[13px] text-ink-soft">
                Showing checks that cite {filterDoc ? <strong className="font-semibold text-ink">{filterDoc.label}</strong> : docFilter}.
              </p>
            )}
          </div>
          {docFilter && (
            <button
              type="button"
              onClick={() => setParams({})}
              className="inline-flex h-8 items-center gap-1 rounded-full px-3 text-xs font-medium text-link hover:bg-sunk"
            >
              <Icon name="x" size={12} /> Show all
            </button>
          )}
        </div>

        {findings.isError && <p className="liquid-glass-strong rounded-3xl p-5 text-short">Couldn't load the cost checks. Check that the backend is running.</p>}
        {findings.isLoading && <div className="h-40 animate-pulse rounded-3xl bg-sunk" />}
        {findings.data && shown.length === 0 && (
          <div className="liquid-glass flex items-start gap-3 rounded-3xl p-6">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-safe-tint text-safe">
              <Icon name="check" size={15} />
            </span>
            <div>
              <p className="text-[15px] font-semibold">
                {docFilter ? "No cost check cites this document yet." : "No cost changes to check."}
              </p>
              <p className="text-[13px] text-ink-soft">
                {docFilter
                  ? "When a bill or notice from this party arrives, Vero checks it against this document automatically."
                  : "New bills are checked automatically when they arrive."}
              </p>
            </div>
          </div>
        )}
        {shown.map((f) => (
          <FindingView key={f.finding_id} finding={f} />
        ))}
      </section>

      <Digest />
      <CostMemory />
    </div>
  );
}

const sourceBadge: Record<KnownDocument["source"], { label: string; cls: string } | null> = {
  history: null,
  contract_check: { label: "From contract check", cls: "bg-action-tint text-link" },
  inbox: { label: "From your inbox", cls: "bg-cream text-ask" },
};

function KnownDocuments({ docs, highlight }: { docs: KnownDocument[]; highlight: string | null }) {
  return (
    <section aria-labelledby="docs-h">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="docs-h" className="text-[20px] font-semibold">What Vero knows</h2>
          <p className="text-[13px] text-ink-soft">Documents every cost is checked against.</p>
        </div>
        <Link to="/contract" className="inline-flex h-8 items-center gap-1 rounded-full px-3 text-xs font-medium text-link hover:bg-sunk">
          Add a contract or lease <Icon name="chevron" size={12} className="-rotate-90" />
        </Link>
      </div>
      <ul className="flex flex-wrap gap-2">
        {docs.map((d) => {
          const badge = sourceBadge[d.source];
          const on = d.doc_id === highlight;
          return (
            <li key={d.doc_id}>
              <Link
                to={on ? "/costs" : `/costs?doc=${encodeURIComponent(d.doc_id)}`}
                aria-current={on ? "true" : undefined}
                className={`arrive inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs ${
                  on ? "btn-primary" : "liquid-glass-pill"
                }`}
              >
                <Icon name="file" size={12} className={on ? "opacity-70" : "text-ink-soft"} />
                <span className="font-medium">{d.label}</span>
                <span className={on ? "opacity-70" : "text-ink-muted"}>· {d.note}</span>
                {badge && (
                  <span className={`ml-1 rounded-full px-2 py-0.5 text-[10px] font-semibold ${on ? "bg-on-hero/20" : badge.cls}`}>
                    {badge.label}
                  </span>
                )}
              </Link>
            </li>
          );
        })}
        {docs.length === 0 && <li className="text-[13px] text-ink-soft">Nothing on file yet.</li>}
      </ul>
    </section>
  );
}

/** Quiet, not idle: legitimate changes never alert. */
function Digest() {
  const { data } = useDigest();
  if (!data?.length) return null;
  return (
    <section aria-labelledby="digest-h">
      <div className="mb-3">
        <h2 id="digest-h" className="text-[20px] font-semibold">Handled quietly</h2>
        <p className="text-[13px] text-ink-soft">Costs that changed, and the evidence that explains them. No action needed.</p>
      </div>
      <ul className="liquid-glass-strong divide-y divide-line overflow-hidden rounded-3xl">
        {data.map((r) => (
          <li key={r.id} className="px-5 py-4 sm:px-6">
            <div className="flex flex-wrap items-center gap-2">
              <ClassificationBadge value={r.classification} />
              <span className="font-mono text-[11px] text-ink-muted">{r.doc_id}</span>
            </div>
            <p className="mt-1.5 text-[14px] font-semibold">{r.title}</p>
            <p className="text-[13px] text-ink-soft">{r.detail}</p>
            {r.calc && (
              <ul className="glass-inset mt-2 space-y-0.5 rounded-lg px-3 py-2 font-mono text-[11px] text-ink-soft">
                {r.calc.map((c) => (
                  <li key={c} className="whitespace-pre-wrap">{c}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** What Vero already knows, from the bank statement. */
function CostMemory() {
  const { data } = useCostMemory();
  if (!data?.length) return null;
  const months = data[0].months;
  return (
    <section aria-labelledby="mem-h">
      <div className="mb-3">
        <h2 id="mem-h" className="text-[20px] font-semibold">Cost Memory</h2>
        <p className="text-[13px] text-ink-soft">Monthly totals from your bank statement since January. April runs to the 13th.</p>
      </div>
      <div className="liquid-glass-strong overflow-x-auto rounded-3xl">
        <table className="w-full min-w-[600px] text-[13px]">
          <thead className="glass-inset border-b border-line text-xs text-ink-soft">
            <tr>
              <th scope="col" className="px-5 py-2 text-left font-medium">Cost</th>
              {months.map((m) => (
                <th key={m.period} scope="col" className="px-3 py-2 text-right font-medium">
                  {monthShort(m.period)}
                  {m.partial && <span className="font-normal text-ink-muted"> (to 13th)</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="num">
            {data.map((row) => (
              <tr key={row.label} className="border-t border-line first:border-t-0">
                <th scope="row" className="px-5 py-3 text-left font-normal">
                  <span className="block font-medium">{row.label}</span>
                  <span className="block text-[11px] text-ink-muted">{row.vendor}</span>
                </th>
                {row.months.map((m, i) => {
                  const prev = row.months[i - 1];
                  const changed = i > 0 && !m.partial && m.value !== prev.value;
                  return (
                    <td key={m.period} className={`px-3 py-3 text-right ${m.partial ? "text-ink-muted" : changed ? "font-semibold text-ink" : "text-ink-soft"}`}>
                      {m.value === 0 ? "—" : eur(m.value)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
