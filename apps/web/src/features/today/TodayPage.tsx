import type { Company, Decision, Forecast } from "../../api/types";
import { useCompany, useDecisions, useForecast } from "../../api/queries";
import { useLive } from "../../api/live";
import { Icon } from "../../components/Icon";
import { agentName } from "../../lib/labels";
import { formatDate, formatEurWhole } from "../../lib/format";
import { CashChart, ForecastLegend } from "../cash/CashChart";
import { btnPrimary, btnQuiet, cardId, DecisionCard, useDecide } from "./DecisionCard";
import { DoneList } from "./DoneList";

const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const weekday = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

/** The headline and one supporting line: where the owner stands. */
function headline(f: Forecast, buffer: number, needsOk: number, suggestions: number) {
  const low = `${formatEurWhole(f.lowestBalanceEur)} on ${formatDate(f.lowestDate)}`;
  const actual = f.weeks.filter((w) => w.isActual);
  if (actual.length) {
    const lowActual = actual.reduce((a, b) => ((b.lowestEur ?? b.balanceEur) < (a.lowestEur ?? a.balanceEur) ? b : a));
    return {
      title: `${actual.length} of 13 weeks have happened. Here's how the forecast held up.`,
      sub: `Bars show the real balance, the line shows the forecast from ${formatDate(f.madeOn)}. Lowest so far: ${formatEurWhole(
        lowActual.lowestEur ?? lowActual.balanceEur,
      )}${f.overdraftLimitEur ? `, inside your ${formatEurWhole(f.overdraftLimitEur)} overdraft facility` : ""}.`,
    };
  }
  if (f.firstShortDate) {
    if (f.overdraftLimitEur && -f.lowestBalanceEur <= f.overdraftLimitEur) {
      return {
        title: "The June gap is covered by your overdraft facility.",
        sub: `The balance still goes down to ${low}, inside your ${formatEurWhole(f.overdraftLimitEur)} facility (simulated). ${
          suggestions > 0 ? `${count(suggestions, "idea", "ideas")} can make the gap smaller.` : ""
        }`,
      };
    }
    return {
      title: `Your account goes into overdraft from the week of ${formatDate(f.firstShortDate)}.`,
      sub: `The June shutdown stops your card sales while fixed costs go on. The balance reaches ${low}. ${
        needsOk > 0 ? "Your CFO has a fix ready." : "Your CFO is working on a fix."
      }`,
    };
  }
  const extra =
    needsOk > 0
      ? `${count(needsOk, "decision needs", "decisions need")} you.`
      : suggestions > 0
        ? `${count(suggestions, "idea", "ideas")} to review.`
        : "Nothing needs you today.";
  return {
    title: "Your cash stays above zero for the next 13 weeks.",
    sub: `The lowest point is ${low}, ${formatEurWhole(f.lowestBalanceEur - buffer)} ${
      f.lowestBalanceEur >= buffer ? "above" : "below"
    } your safety buffer. ${extra}`,
  };
}

const order = (a: Decision, b: Decision) => (b.resolvedAt ?? b.createdAt).localeCompare(a.resolvedAt ?? a.createdAt);

/** Dark pill for a secondary call to action, e.g. "See suggestion" (navy in light, near-black in dark). */
const btnStrong = btnPrimary.replace("btn-primary", "btn-strong");

const goTo = (id: string) => {
  const el = document.getElementById(cardId(id));
  el?.scrollIntoView({ behavior: "smooth", block: "start" });
  el?.focus({ preventScroll: true });
};

/** Sand-edged card beside the chart: the one thing that needs the owner. */
function NeedsOkSummary({ d, more }: { d: Decision; more: number }) {
  const { act, pending } = useDecide(d);
  const amount = d.offer?.amountEur ?? d.amountEur;
  return (
    <section
      aria-labelledby="ok-sum-h"
      className="liquid-glass-strong flex flex-col gap-4 rounded-3xl p-5 sm:flex-row sm:items-center sm:gap-8 sm:px-6"
    >
      <div className="shrink-0">
        <p className="flex items-center gap-2 text-[14px] font-semibold text-ask">
          <span className="h-2 w-2 rounded-full bg-sand" aria-hidden="true" />
          Needs your OK{more > 0 && <span className="font-medium"> · {more} more</span>}
        </p>
        <h2 id="ok-sum-h" className="mt-2 text-sm font-semibold">
          {d.offer?.name ?? agentName[d.agent]}
        </h2>
        {amount !== undefined ? (
          <p className="num tight text-[28px] font-semibold leading-tight">{formatEurWhole(amount)}</p>
        ) : (
          <p className="tight text-[20px] font-semibold leading-snug">{d.headline}</p>
        )}
      </div>
      <p className="line-clamp-3 min-w-0 flex-1 text-[13px] text-ink-soft">
        {amount !== undefined ? d.headline + ". " : ""}
        {d.summary}
      </p>
      <div className="flex shrink-0 gap-1">
        <button type="button" className={btnPrimary} onClick={() => goTo(d.id)}>
          Review
        </button>
        <button type="button" className={btnQuiet} disabled={pending} onClick={() => act("reject")}>
          {d.rejectLabel ?? "Not now"}
        </button>
      </div>
    </section>
  );
}

function AllClearSummary({ suggestion }: { suggestion?: Decision }) {
  return (
    <section
      aria-labelledby="ok-sum-h"
      className="liquid-glass-strong flex flex-col gap-4 rounded-3xl p-5 sm:flex-row sm:items-center sm:gap-8 sm:px-6"
    >
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-[14px] font-semibold text-safe">
          <span className="h-2 w-2 rounded-full bg-safe" aria-hidden="true" />
          Nothing needs your OK
        </p>
        <h2 id="ok-sum-h" className="tight mt-2 text-[20px] font-semibold leading-snug">
          {suggestion ? suggestion.headline : "You're all set for today."}
        </h2>
        <p className="mt-1 line-clamp-2 text-[13px] text-ink-soft">
          {suggestion ? suggestion.summary : "Your agents keep watching your accounts and invoices."}
        </p>
      </div>
      {suggestion && (
        <div className="shrink-0">
          <button type="button" className={btnStrong} onClick={() => goTo(suggestion.id)}>
            See suggestion
          </button>
        </div>
      )}
    </section>
  );
}

/** "Everything checked at 07:02" + the live agent status line. */
function CheckedCard({ at }: { at?: string }) {
  const { latest } = useLive();
  return (
    <div className="liquid-glass flex items-center gap-3 rounded-3xl p-5">
      <span
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-[1.5px] ${
          latest.busy ? "border-link text-link" : "border-line-strong text-ink-soft"
        }`}
      >
        <Icon name="check" size={16} className={`[stroke-width:1.5] ${latest.busy ? "animate-pulse" : ""}`} />
      </span>
      <div className="min-w-0" aria-live="polite">
        <p className="text-[13px] font-semibold">
          {latest.busy ? "Checking now…" : at ? `Everything checked at ${time(at)}` : "Checking…"}
        </p>
        <p className="text-xs text-ink-soft">
          {agentName[latest.agent]}: {latest.message}
        </p>
      </div>
    </div>
  );
}

function Summary({ company, f }: { company: Company; f: Forecast }) {
  const buffer = company.safetyBufferEur;
  const negWeeks = f.weeks.filter((w) => w.balanceEur < 0).length;
  const cells = [
    {
      label: "Lowest point",
      value: formatEurWhole(f.lowestBalanceEur),
      alert: f.lowestBalanceEur < 0,
      sub: (
        <span className={f.lowestBalanceEur < buffer ? "text-short" : "text-safe"}>
          {formatDate(f.lowestDate)} · W{f.lowestWeek}
        </span>
      ),
    },
    {
      label: "Weeks below zero",
      value: String(negWeeks),
      alert: negWeeks > 0,
      sub: negWeeks ? `First: week of ${formatDate(f.firstShortDate!)}` : "None in the next 13 weeks",
    },
    { label: "Safety buffer", value: formatEurWhole(buffer), sub: "Minimum you want to keep" },
    {
      label: "Overdraft facility",
      value: f.overdraftLimitEur ? formatEurWhole(f.overdraftLimitEur) : "None",
      sub: f.overdraftLimitEur ? "Arranged (simulated)" : "Not arranged yet",
    },
  ];
  return (
    <section aria-labelledby="sum-h">
      <h2 id="sum-h" className="mb-2 text-[15px] font-semibold">Next 13 weeks</h2>
      <dl className="liquid-glass-strong grid grid-cols-2 rounded-3xl lg:grid-cols-4">
        {cells.map((c, i) => (
          <div
            key={c.label}
            className={`border-line px-5 py-3 sm:px-6 sm:py-4 ${i % 2 ? "border-l" : ""} ${i > 1 ? "border-t lg:border-t-0" : ""} ${
              i === 2 ? "lg:border-l" : ""
            }`}
          >
            <dt className="text-xs text-ink-soft">{c.label}</dt>
            <dd className={`num tight mt-0.5 text-[22px] font-semibold ${c.alert ? "text-short" : ""}`}>{c.value}</dd>
            <dd className="mt-0.5 text-xs text-ink-soft">{c.sub}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function SectionHeader({ id, title, sub }: { id: string; title: string; sub?: string }) {
  return (
    <div className="mb-3">
      <h2 id={id} className="text-[20px] font-semibold">{title}</h2>
      {sub && <p className="text-[13px] text-ink-soft">{sub}</p>}
    </div>
  );
}

export function TodayPage() {
  const { data: company } = useCompany();
  const decisions = useDecisions();
  const forecast = useForecast();

  const all = decisions.data ?? [];
  const needsOk = all.filter((d) => d.status === "pending" && d.autonomy === "approval_required");
  const suggestions = all.filter((d) => d.status === "pending" && d.autonomy === "recommendation");
  const handled = all.filter((d) => d.status !== "pending").sort(order);

  const f = forecast.data;
  const actuals = !!f?.weeks.some((w) => w.isActual);
  const head = f && company ? headline(f, company.safetyBufferEur, needsOk.length, suggestions.length) : null;

  return (
    <div className="space-y-10">
      {/* First screen: headline, forecast and the 13-week summary, visible together without scrolling */}
      <div className="space-y-6 [@media(min-width:640px)_and_(max-height:860px)]:space-y-4">
      {/* Headline */}
      <section aria-labelledby="page-h">
        <p className="text-[14px] text-ink-soft">
          {company ? `${weekday(company.asOf)} · ${company.name}${company.city ? `, ${company.city}` : ""}` : " "}
        </p>
        {head ? (
          <>
            <h1
              id="page-h"
              className="mt-2 max-w-[28ch] text-[32px] font-semibold leading-[1.1] tracking-[-0.025em] sm:text-[38px] lg:text-[40px] [@media(min-width:640px)_and_(max-height:860px)]:mt-1 [@media(min-width:640px)_and_(max-height:860px)]:max-w-none [@media(min-width:640px)_and_(max-height:860px)]:text-[30px]"
            >
              {head.title}
            </h1>
            <p className="mt-2 max-w-[80ch] text-[15px] text-ink-soft">{head.sub}</p>
          </>
        ) : (
          <>
            <h1 id="page-h" className="sr-only">Overview</h1>
            <div className="mt-2 h-10 max-w-xl animate-pulse rounded-xl bg-sunk" />
            <div className="mt-3 h-5 max-w-md animate-pulse rounded-lg bg-sunk" />
          </>
        )}
      </section>

      {/* Hero: full-width forecast */}
      <div>
        <section aria-labelledby="chart-h" className="liquid-glass liquid-navy min-w-0 rounded-3xl p-5 text-on-hero sm:px-7 sm:py-6">
          <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
            <div>
              <h2 id="chart-h" className="text-[14px] font-medium tracking-normal text-on-hero/80">
                {actuals ? `In the bank on ${company ? formatDate(company.asOf) : ""} · forecast vs actual` : "In the bank today · 13-week cash forecast"}
              </h2>
              <p className="num mt-1 text-[38px] font-semibold leading-none tracking-[-0.03em] sm:text-[48px]">
                {company ? formatEurWhole(company.cashTodayEur) : "—"}
              </p>
            </div>
            <ForecastLegend bufferEur={company?.safetyBufferEur} range={!f || f.days.some((d) => d.lowEur !== undefined)} />
          </div>
          <div className="-mx-1 mt-2">
            {forecast.isError && (
              <p className="py-10 text-center text-sand">Couldn't load the forecast. Check that the backend is running.</p>
            )}
            {f ? (
              <CashChart
                days={f.days}
                weeks={f.weeks}
                bufferEur={company?.safetyBufferEur ?? f.weeks[0]?.bufferEur ?? 0}
                today={company?.asOf ?? f.madeOn}
                lowest={{ value: f.lowestBalanceEur, date: f.lowestDate, week: f.lowestWeek }}
              />
            ) : (
              !forecast.isError && <div className="mt-4 h-[300px] animate-pulse rounded-xl bg-on-hero/5 sm:h-[380px]" />
            )}
          </div>
        </section>
      </div>

      {f && company && <Summary company={company} f={f} />}
      </div>

      {/* What needs you, and when Vero last checked */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        {decisions.isError ? (
          <p className="liquid-glass-strong rounded-3xl p-5 text-short">Couldn't load your items. Check that the backend is running.</p>
        ) : needsOk[0] ? (
          <NeedsOkSummary d={needsOk[0]} more={needsOk.length - 1} />
        ) : decisions.data ? (
          <AllClearSummary suggestion={suggestions[0]} />
        ) : (
          <div className="h-28 animate-pulse rounded-3xl bg-sunk" />
        )}
        <CheckedCard at={f?.generatedAt} />
      </div>

      {/* Needs your OK */}
      {needsOk.length > 0 && (
        <section aria-labelledby="ok-h">
          <SectionHeader id="ok-h" title="Needs your OK" sub="Nothing happens until you decide." />
          <div className="space-y-3">
            {needsOk.map((d) => (
              <DecisionCard key={d.id} decision={d} />
            ))}
          </div>
        </section>
      )}

      {/* Suggestions */}
      {suggestions.length > 0 && (
        <section aria-labelledby="sugg-h">
          <SectionHeader id="sugg-h" title="Suggestions" sub="You can ignore these. Nothing happens unless you say yes." />
          <div className="space-y-3">
            {suggestions.map((d) => (
              <DecisionCard key={d.id} decision={d} />
            ))}
          </div>
        </section>
      )}

      {/* Done and checked */}
      {handled.length > 0 && (
        <section aria-labelledby="done-h">
          <SectionHeader
            id="done-h"
            title="Done and checked"
            sub="What your agents did on their own, and what you decided. Open a row to see why."
          />
          <DoneList items={handled} />
        </section>
      )}
    </div>
  );
}
