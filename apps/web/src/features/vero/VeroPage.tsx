import { Link, useSearchParams } from "react-router-dom";
import { useAcknowledgeFinding, useCompany, useCostDocuments, useCostMemory, useDigest, useFindings } from "../../api/queries";
import type { CostMemoryRow, Digest, Finding, KnownDocument } from "../../api/types";
import { Icon } from "../../components/Icon";
import { useToast } from "../../components/Toast";
import { formatDate } from "../../lib/format";
import { FindingView, isWorking } from "./Investigation";
import { ClassificationBadge, eur } from "./parts";

const cites = (f: Finding, docId: string) => f.source_doc_id === docId || f.evidence_refs.some((e) => e.doc_id === docId);

function headline(cards: Finding[]) {
  const open = cards.filter((f) => f.status !== "resolved");
  const needsOk = open.filter((f) => f.status === "pending_approval");
  if (needsOk.length) {
    const atStake = needsOk.reduce((s, f) => s + (f.financial_impact?.disputed_annual ?? 0), 0);
    const who = needsOk.length === 1 ? "One cost change needs" : `${needsOk.length} cost changes need`;
    return atStake > 0 ? `${who} you. ${eur(atStake)} a year is at stake.` : `${who} you.`;
  }
  if (open.some(isWorking)) return "A cost changed without a reason on file. Vero is looking into it.";
  if (open.some((f) => f.classification === "escalated")) return "Vero couldn't explain a cost change. It needs you to look.";
  return "Nothing needs you. Every change so far is explained by your documents.";
}

/** Vero: finds out why a recurring cost changed, proves what to pay, fixes what depends on it. */
export function VeroPage() {
  const [params, setParams] = useSearchParams();
  const docFilter = params.get("doc");
  const { data: company } = useCompany();
  const findings = useFindings();
  const docs = useCostDocuments();
  const digest = useDigest();

  // Quiet findings (verified, nothing to decide) belong in "Handled quietly", not as cards.
  const cards = (findings.data ?? []).filter((f) => !f.quiet);
  const shown = docFilter ? cards.filter((f) => cites(f, docFilter)) : cards;
  const counts = digest.data?.counts;
  const needsYou = counts?.needs_owner ?? 0;
  const filterDoc = docs.data?.find((d) => d.doc_id === docFilter);

  return (
    <div className="space-y-10">
      <section aria-labelledby="vero-h">
        <p className="flex flex-wrap items-center gap-2 text-xs text-ink-soft">
          <span className="rounded-full bg-sunk px-2 py-0.5 font-semibold text-ink">Vero · Cost check</span>
          {company ? `${company.name}${company.city ? `, ${company.city}` : ""}` : " "}
        </p>
        <h1 id="vero-h" className="mt-2 max-w-[30ch] text-[32px] font-semibold leading-[1.1] tracking-[-0.025em] sm:text-[44px] lg:text-[48px]">
          {findings.data ? headline(cards) : findings.isError ? "Your recurring costs, and what Vero expects them to be." : "Checking your costs…"}
        </h1>
        <p className="mt-2 max-w-[80ch] text-[15px] text-ink-soft">
          Vero checks every bill and recurring cost against your bank history, contracts and leases. When something changes
          without a reason on file, it finds the reason, proves what you should pay, and prepares the changes for your OK.
        </p>
      </section>

      {/* The digest's counts (GET /digest): 3 / 3 / 0 / 3 after a reset, 4 / 3 / 1 / 3 after the rent upload. */}
      <dl className="liquid-glass grid grid-cols-2 rounded-3xl lg:grid-cols-4">
        {[
          { k: "Changes checked", v: counts ? String(counts.changes) : "…", sub: "In this week's digest" },
          { k: "Handled quietly", v: counts ? String(counts.handled_quietly) : "…", sub: "Explained by the evidence, no alert" },
          { k: "Needs you", v: counts ? String(needsYou) : "…", sub: needsYou ? "See below" : "Nothing waiting", alert: needsYou > 0 },
          { k: "Routine checks", v: counts ? String(counts.routine_checks) : "…", sub: "Payments that matched their contract" },
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

        {findings.isError && (
          <p role="alert" className="liquid-glass-strong rounded-3xl p-5 text-short">Couldn't load the cost checks. Check that the backend is running.</p>
        )}
        {findings.isLoading && <div className="h-40 animate-pulse rounded-3xl bg-sunk" />}
        {findings.data && shown.length === 0 && (
          <div className="liquid-glass flex flex-col gap-3 rounded-3xl p-6 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-safe-tint text-safe">
                <Icon name="check" size={15} />
              </span>
              <div>
                <p className="text-[15px] font-semibold">
                  {docFilter ? "No cost check that needs you cites this document." : "No cost change needs a closer look."}
                </p>
                <p className="text-[13px] text-ink-soft">
                  {docFilter
                    ? "When a bill or notice from this party arrives, Vero checks it against this document automatically."
                    : "The changes Vero explained on its own are under Handled quietly."}
                </p>
              </div>
            </div>
            {!docFilter && (
              <Link to="/contract" className="liquid-glass-pill inline-flex h-9 shrink-0 items-center gap-1.5 self-start rounded-full px-4 text-[13px] font-semibold sm:self-center">
                <Icon name="upload" size={14} /> Upload a bill or notice
              </Link>
            )}
          </div>
        )}
        {shown.map((f) => (
          <FindingView key={f.finding_id} finding={f} />
        ))}
      </section>

      {digest.data && <DigestSection digest={digest.data} findings={findings.data ?? []} />}
      {digest.isError && <p role="alert" className="text-[13px] text-short">Couldn't load this week's digest.</p>}
      <CostMemory />
    </div>
  );
}

const sourceBadge: Record<KnownDocument["source"], { label: string; cls: string } | null> = {
  history: null,
  contract_check: { label: "From contract check", cls: "bg-action-tint text-link" },
  inbox: { label: "From your inbox", cls: "bg-cream text-ask" },
  upload: { label: "Uploaded", cls: "bg-action-tint text-link" },
};

function KnownDocuments({ docs, highlight }: { docs: KnownDocument[]; highlight: string | null }) {
  return (
    <section aria-labelledby="docs-h">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="docs-h" className="text-[20px] font-semibold">What Vero knows</h2>
          <p className="text-[13px] text-ink-soft">Documents every cost is checked against. Tap one to see the checks that cite it.</p>
        </div>
        <Link to="/contract" className="inline-flex h-8 items-center gap-1 rounded-full px-3 text-xs font-medium text-link hover:bg-sunk">
          Upload a bill or notice <Icon name="chevron" size={12} className="-rotate-90" />
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

const money = (v: number, currency: string) =>
  currency === "EUR" ? eur(v) : new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(v);

/** Quiet, not idle: legitimate changes never alert. One tap clears them; routine checks show what matched. */
function DigestSection({ digest, findings }: { digest: Digest; findings: Finding[] }) {
  const toast = useToast();
  const ack = useAcknowledgeFinding();
  const quiet = digest.changes.filter((c) => c.quiet);
  const byId = new Map(findings.map((f) => [f.finding_id, f]));
  if (!quiet.length && !digest.routine_checks.length) return null;

  return (
    <section aria-labelledby="digest-h" className="space-y-4">
      <div>
        <h2 id="digest-h" className="text-[20px] font-semibold">Handled quietly</h2>
        <p className="text-[13px] text-ink-soft">
          Costs that changed, and the evidence that explains them. No action needed: mark one as seen to clear it.
        </p>
      </div>
      {quiet.length > 0 && (
        <ul className="liquid-glass-strong divide-y divide-line overflow-hidden rounded-3xl">
          {quiet.map((c) => {
            const searched = byId.get(c.finding_id)?.investigation?.searched ?? [];
            const busy = ack.isPending && ack.variables === c.finding_id;
            return (
              <li key={c.finding_id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:px-6">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <ClassificationBadge value={c.classification} />
                    {c.investigation_mode && (
                      <span className="text-[11px] text-ink-muted">
                        after a look-up · {c.investigation_mode === "agent" ? "Vero's agent" : "Vero's own steps"}
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-[14px] font-semibold">{c.title}</p>
                  <p className="text-[13px] text-ink-soft">{c.reason}</p>
                  {searched.length > 0 && (
                    <ol className="glass-inset mt-2 space-y-0.5 rounded-lg px-3 py-2 text-[11px] text-ink-soft">
                      {searched.map((s, i) => (
                        <li key={i}>
                          {i + 1}. {s}
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={async () => {
                    try {
                      await ack.mutateAsync(c.finding_id);
                    } catch {
                      toast("Couldn't clear that one. Try again.", "neutral");
                    }
                  }}
                  className="liquid-glass-pill inline-flex h-8 shrink-0 items-center gap-1 self-start rounded-full px-3 text-xs font-semibold"
                >
                  <Icon name="check" size={12} /> {busy ? "Clearing…" : "Seen"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {digest.routine_checks.length > 0 && (
        <div>
          <h3 className="mb-2 text-[15px] font-semibold">Routine checks</h3>
          <ul className="liquid-glass divide-y divide-line overflow-hidden rounded-3xl">
            {digest.routine_checks.map((r) => (
              <li key={r.cost_ref} className="flex flex-wrap items-baseline justify-between gap-2 px-5 py-3 text-[13px] sm:px-6">
                <span className="flex min-w-0 items-center gap-2">
                  <Icon name="check" size={13} className="shrink-0 text-safe" />
                  <span className="font-medium">{r.counterparty}</span>
                  <span className="shrink-0 text-ink-muted">· {formatDate(r.date)}</span>
                </span>
                <span className="num text-ink-soft">
                  {money(r.paid, r.currency)} paid, matches the contract
                  {r.eur_amount !== undefined && <span className="text-ink-muted"> (≈ {eur(r.eur_amount)})</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

const sourceName: Record<CostMemoryRow["sources"][number], string> = {
  statement: "Bank statement",
  contract: "Contract",
  price_list: "Price list",
  invoice: "Invoices",
};

const chf = new Intl.NumberFormat("en-GB", { style: "currency", currency: "CHF" });

/** "1" → "1st", "10" → "10th" */
const ordinal = (n: number) => `${n}${n % 10 === 1 && n !== 11 ? "st" : n % 10 === 2 && n !== 12 ? "nd" : n % 10 === 3 && n !== 13 ? "rd" : "th"}`;

/** What Vero expects each recurring cost to be, and the evidence behind it. */
function CostMemory() {
  const { data } = useCostMemory();
  if (!data?.length) return null;
  return (
    <section aria-labelledby="mem-h">
      <div className="mb-3">
        <h2 id="mem-h" className="text-[20px] font-semibold">Cost Memory</h2>
        <p className="text-[13px] text-ink-soft">What Vero expects each recurring cost to be, from your bank statement, contracts and price lists.</p>
      </div>
      <div className="liquid-glass-strong overflow-x-auto rounded-3xl">
        <table className="w-full min-w-[600px] text-[13px]">
          <thead className="glass-inset border-b border-line text-xs text-ink-soft">
            <tr>
              <th scope="col" className="px-5 py-2 text-left font-medium">Cost</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Expected each month</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Due</th>
              <th scope="col" className="px-5 py-2 text-left font-medium">Based on</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.cost_ref} className="border-t border-line first:border-t-0">
                <th scope="row" className="px-5 py-3 text-left font-normal">
                  <span className="block font-medium">{row.label}</span>
                  <span className="block text-[11px] text-ink-muted">{row.vendor}</span>
                </th>
                <td className="num px-3 py-3 text-right">
                  {row.expected ? (
                    <>
                      <span className="font-medium">
                        {row.expected.currency === "CHF" ? chf.format(row.expected.amount) : eur(row.expected.amount)}
                      </span>
                      {row.expected.eur_amount !== undefined && (
                        <span className="block text-[11px] text-ink-muted">≈ {eur(row.expected.eur_amount)} at the last rate</span>
                      )}
                    </>
                  ) : (
                    <span className="text-ink-soft">
                      Per invoice{row.prices > 0 && <span className="block text-[11px] text-ink-muted">{row.prices} list prices on file</span>}
                    </span>
                  )}
                </td>
                <td className="num px-3 py-3 text-right text-ink-soft">{row.due_day ? ordinal(row.due_day) : "—"}</td>
                <td className="px-5 py-3">
                  <span className="flex flex-wrap gap-1">
                    {row.sources.map((s) => (
                      <span key={s} className="rounded-full bg-sunk px-2 py-0.5 text-[11px] text-ink-soft">
                        {sourceName[s]}
                      </span>
                    ))}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
