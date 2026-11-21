import type { CSSProperties } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  LabelList,
  Line,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import type { ForecastDay, ForecastWeek, RiskLevel } from "../../api/types";
import { formatDate, formatEurK1, formatEurShort, formatEurWhole } from "../../lib/format";

/*
  Drawn on the forecast card (solid navy in light, navy glass in dark), so one palette serves
  both themes. SVG attributes can't read CSS variables reliably, so the chart keeps its own palette.
  - Solid periwinkle bar = this week, or a week that already happened (actual).
  - Dashed white outline = forecast.
  - Sand fill            = below the safety buffer.
  - Solid coral          = below zero (overdraft).
*/
const SOLID = "#7c87d6";
const SAND = "#f2d9a0";
const CORAL = "#ef8a74";
const WHITE_SOFT = "rgba(255,255,255,0.7)";
const WHITE_DIM = "rgba(255,255,255,0.55)";
const WHITE_FAINT = "rgba(255,255,255,0.08)";
const OUTLINE = "rgba(255,255,255,0.35)";
const COMPARE = "rgba(255,255,255,0.8)";
const LABEL_INK = "#1b2547";
const LABEL_MUTED = "#535a6e";
const LABEL_SHORT = "#b3402b";

const STATUS_LABEL: Record<RiskLevel, string> = {
  green: "Above your buffer",
  yellow: "Below your buffer",
  red: "Overdraft",
};
const STATUS_DOT: Record<RiskLevel, string> = { green: "var(--ok)", yellow: "var(--ask)", red: "var(--short)" };

/** Round axis steps: 1k, 2k, 2.5k, 5k, 10k, ... including negatives. `parts` ≈ how many steps to aim for. */
function niceTicks(values: number[], parts = 4): number[] {
  const max = Math.max(0, ...values);
  const min = Math.min(0, ...values);
  const raw = (max - min) / parts || 1_000;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const ticks: number[] = [];
  for (let t = Math.floor(min / step) * step; t < max + step; t += step) ticks.push(Math.round(t));
  return ticks;
}

function barStyle(w: ForecastWeek, index: number) {
  if (w.status === "red") return { fill: CORAL, stroke: "none" };
  if (w.isActual || index === 0) return { fill: SOLID, stroke: w.status === "yellow" ? SAND : "none", strokeWidth: 1.5 };
  if (w.status === "yellow") return { fill: SAND, stroke: "none" };
  return { fill: "rgba(255,255,255,0.04)", stroke: OUTLINE, strokeDasharray: "4 3", strokeWidth: 1 };
}

export interface LowestPoint {
  value: number;
  date: string;
  week: number;
}

interface Props {
  weeks: ForecastWeek[];
  /** Optional comparison line, e.g. "without the contract". Weeks with forecastEur draw their own line. */
  compareWeeks?: ForecastWeek[];
  compareLabel?: string;
  /** Fixed height in px. If omitted: 300px on mobile, 380px from sm up. */
  height?: number;
  caption?: string;
  /** Pin a label on the lowest point and mark the first week below zero. Default true. */
  annotate?: boolean;
  /** Lowest daily balance (from the forecast). Defaults to the lowest weekly bar. */
  lowest?: LowestPoint;
  overdraftLimitEur?: number;
}

type Row = ForecastWeek & { label: string; compare?: number };

function ForecastTooltip({ active, payload, compareLabel }: TooltipContentProps<number, string> & { compareLabel: string }) {
  if (!active || !payload?.length) return null;
  const w = payload[0].payload as Row;
  const gap = w.balanceEur - w.bufferEur;
  return (
    <div className="liquid-glass-strong min-w-60 rounded-xl p-3 text-xs text-ink">
      <p className="font-semibold">
        W{w.week} · week of {formatDate(w.startDate)}
        {w.isActual && <span className="ml-1.5 font-medium text-ink-soft">(actual)</span>}
      </p>
      <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-ink-soft">
        <dt>Cash at end of week</dt>
        <dd className={`num text-right font-semibold ${w.balanceEur < 0 ? "text-short" : "text-ink"}`}>{formatEurWhole(w.balanceEur)}</dd>
        {w.lowestEur !== undefined && w.lowestEur < w.balanceEur && (
          <>
            <dt>Lowest day</dt>
            <dd className={`num text-right ${w.lowestEur < 0 ? "text-short" : "text-ink"}`}>{formatEurWhole(w.lowestEur)}</dd>
          </>
        )}
        {(w.inflowEur !== 0 || w.outflowEur !== 0) && (
          <>
            <dt>Money in</dt>
            <dd className="num text-right text-ink">{formatEurWhole(w.inflowEur)}</dd>
            <dt>Money out</dt>
            <dd className="num text-right text-ink">{formatEurWhole(-Math.abs(w.outflowEur))}</dd>
          </>
        )}
        <dt>vs safety buffer</dt>
        <dd className={`num text-right font-medium ${gap < 0 ? "text-short" : "text-ink"}`}>
          {gap >= 0 ? "+" : ""}
          {formatEurWhole(gap)}
        </dd>
        {w.compare !== undefined && (
          <>
            <dt>{compareLabel}</dt>
            <dd className="num text-right text-ink">{formatEurWhole(w.compare)}</dd>
          </>
        )}
      </dl>
      <p className="mt-2 flex items-center gap-1.5 border-t border-line pt-2 text-ink-soft">
        <span className="h-2 w-2 rounded-full" style={{ background: STATUS_DOT[w.status] }} aria-hidden="true" />
        {STATUS_LABEL[w.status]}
      </p>
    </div>
  );
}

function Swatch({ kind }: { kind: "solid" | "forecast" | "close" | "short" }) {
  const style =
    kind === "solid"
      ? { background: SOLID }
      : kind === "short"
        ? { background: CORAL }
        : kind === "close"
          ? { background: SAND }
          : { border: `1px dashed ${OUTLINE}`, background: "rgba(255,255,255,0.04)" };
  return <span className="h-2.5 w-2.5 rounded-[3px]" style={style} aria-hidden="true" />;
}

const lineIcon = (stroke: string, dashed: boolean, width = 1.25) => (
  <svg width="16" height="4" aria-hidden="true">
    <line x1="0" y1="2" x2="16" y2="2" stroke={stroke} strokeWidth={width} strokeDasharray={dashed ? "3 3" : undefined} />
  </svg>
);

/** Legend for the weekly bar chart (Contract check). */
export function WeeklyChartLegend({
  compareLabel,
  bufferEur,
  actuals,
  overdraftLimitEur,
}: {
  compareLabel?: string;
  bufferEur?: number;
  actuals?: boolean;
  overdraftLimitEur?: number;
}) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[13px] text-on-hero/80">
      <li className="inline-flex items-center gap-1.5"><Swatch kind="solid" />{actuals ? "Actual" : "This week"}</li>
      <li className="inline-flex items-center gap-1.5"><Swatch kind="forecast" />Forecast</li>
      <li className="inline-flex items-center gap-1.5"><Swatch kind="close" />Below buffer</li>
      <li className="inline-flex items-center gap-1.5"><Swatch kind="short" />Overdraft</li>
      <li className="inline-flex items-center gap-1.5">
        {lineIcon("rgba(255,255,255,0.6)", true, 1)}
        Safety buffer{bufferEur !== undefined && <span className="num font-semibold text-on-hero">&nbsp;{formatEurWhole(bufferEur)}</span>}
      </li>
      {overdraftLimitEur !== undefined && (
        <li className="inline-flex items-center gap-1.5">
          {lineIcon(CORAL, true)}
          Overdraft limit<span className="num text-on-hero/85">&nbsp;{formatEurWhole(-overdraftLimitEur)}</span>
        </li>
      )}
      {compareLabel && <li className="inline-flex items-center gap-1.5">{lineIcon(COMPARE, false, 2)}{compareLabel}</li>}
    </ul>
  );
}

/** Weekly bars with a comparison line: used by Contract check ("with vs without the contract"). */
export function WeeklyCashChart({
  weeks,
  compareWeeks,
  compareLabel = "Before",
  height,
  caption,
  annotate = true,
  lowest,
  overdraftLimitEur,
}: Props) {
  const buffer = weeks[0]?.bufferEur ?? 0;
  const ownLine = weeks.some((w) => w.forecastEur !== undefined);
  const data: Row[] = weeks.map((w, i) => ({
    ...w,
    label: `W${w.week}`,
    compare: ownLine ? w.forecastEur : compareWeeks?.[i]?.balanceEur,
  }));
  const hasCompare = data.some((d) => d.compare !== undefined);

  const lowIdx = lowest
    ? Math.max(0, data.findIndex((w) => w.week === lowest.week))
    : data.reduce((lo, w, i) => (w.balanceEur < data[lo].balanceEur ? i : lo), 0);
  const low: LowestPoint = lowest ?? { value: data[lowIdx]?.balanceEur ?? 0, date: data[lowIdx]?.startDate ?? "", week: lowIdx + 1 };

  const ticks = niceTicks([
    buffer,
    low.value,
    ...(overdraftLimitEur ? [-overdraftLimitEur] : []),
    ...weeks.map((w) => w.balanceEur),
    ...data.map((w) => w.compare ?? 0),
  ]);
  const yMin = ticks[0];
  const yMax = ticks[ticks.length - 1];
  const firstShortIdx = data.findIndex((w) => w.balanceEur < 0);
  const negWeeks = weeks.filter((w) => w.balanceEur < 0).length;

  const XTick = ({ x, y, payload }: { x?: number | string; y?: number | string; payload?: { value: string; index: number } }) => {
    if (!payload) return <g />;
    const i = payload.index;
    const w = data[i];
    const strong = i === 0 || (annotate && i === lowIdx);
    const fill = annotate && i === lowIdx && w.status !== "green" ? CORAL : strong ? "#ffffff" : WHITE_SOFT;
    const top = Number(y ?? 0);
    return (
      <g>
        <text x={x} y={top + 13} textAnchor="middle" fontSize={13} fontWeight={strong ? 700 : 500} fill={fill}>
          {payload.value}
        </text>
        <text x={x} y={top + 28} textAnchor="middle" fontSize={12} fontWeight={strong ? 600 : 400} fill={strong ? fill : WHITE_DIM}>
          {formatDate(w.startDate)}
        </text>
      </g>
    );
  };

  const LowestLabel = (p: { x?: number | string; y?: number | string; width?: number | string; height?: number | string; index?: number }) => {
    if (!annotate || p.index !== lowIdx) return null;
    const cx = Number(p.x) + Number(p.width) / 2;
    const y = Number(p.y);
    const h = Number(p.height ?? 0);
    const w = 118;
    const below = low.value < 0;
    const top = below ? Math.max(y, y + h) + 12 : Math.min(y, y + h) - 50;
    // Arrow points at the bar: up from a label below a negative bar, down from a label above.
    const arrow = below
      ? `M${cx - 6},${top} L${cx},${top - 6} L${cx + 6},${top} Z`
      : `M${cx - 6},${top + 38} L${cx},${top + 44} L${cx + 6},${top + 38} Z`;
    return (
      <g pointerEvents="none" style={{ filter: "drop-shadow(0 4px 10px rgba(0,0,0,0.25))" }}>
        <rect x={cx - w / 2} y={top} width={w} height={38} rx={10} fill="#ffffff" />
        <path d={arrow} fill="#ffffff" />
        <text x={cx} y={top + 17} textAnchor="middle" fontSize={13} fontWeight={700} fill={low.value < 0 ? LABEL_SHORT : LABEL_INK}>
          {formatEurWhole(low.value)}
        </text>
        <text x={cx} y={top + 31} textAnchor="middle" fontSize={11} fill={LABEL_MUTED}>
          Lowest, {formatDate(low.date)}
        </text>
      </g>
    );
  };

  return (
    <figure>
      <div className="-mx-1 overflow-x-auto pb-1 sm:mx-0 sm:overflow-visible">
        <div
          role="img"
          aria-label={`Cash forecast for ${weeks.length} weeks, from ${formatDate(weeks[0]?.startDate ?? "")}. Safety buffer ${formatEurWhole(
            buffer,
          )}. Lowest ${formatEurWhole(low.value)} on ${formatDate(low.date)}. ${
            negWeeks === 0
              ? "No week closes below zero."
              : `${negWeeks} weeks close below zero, the first in the week of ${formatDate(data[firstShortIdx].startDate)}.`
          }`}
          className={`min-w-[720px] sm:min-w-0 ${height ? "" : "h-[300px] sm:h-[380px]"}`}
          style={height ? { height } : undefined}
        >
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 52, right: 8, left: 0, bottom: 4 }} barCategoryGap="24%">
              <CartesianGrid vertical={false} stroke={WHITE_FAINT} />
              {yMin < 0 && <ReferenceArea y1={yMin} y2={0} fill={CORAL} fillOpacity={0.09} stroke="none" ifOverflow="hidden" />}
              <XAxis dataKey="label" tick={XTick} tickLine={false} axisLine={false} interval={0} height={38} />
              <YAxis
                ticks={ticks}
                domain={[yMin, yMax]}
                tickFormatter={formatEurShort}
                tick={{ fill: WHITE_SOFT, fontSize: 13 }}
                tickLine={false}
                axisLine={false}
                width={58}
              />
              <Tooltip
                cursor={{ fill: "rgba(255,255,255,0.06)" }}
                content={(p) => <ForecastTooltip {...(p as TooltipContentProps<number, string>)} compareLabel={compareLabel} />}
              />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.7)" strokeWidth={1.25} />
              {annotate && firstShortIdx > 0 && firstShortIdx !== lowIdx && (
                <ReferenceLine x={data[firstShortIdx].label} stroke={CORAL} strokeOpacity={0.6} strokeDasharray="2 3" />
              )}
              <Bar dataKey="balanceEur" name="Cash at end of week" radius={[6, 6, 6, 6]} maxBarSize={40} animationDuration={600}>
                {data.map((w, i) => (
                  <Cell key={w.week} {...barStyle(w, i)} />
                ))}
                <LabelList dataKey="balanceEur" content={LowestLabel} />
              </Bar>
              {hasCompare && (
                <Line dataKey="compare" name={compareLabel} stroke={COMPARE} strokeWidth={2} dot={{ r: 2.5, fill: COMPARE, strokeWidth: 0 }} isAnimationActive={false} connectNulls />
              )}
              <ReferenceLine y={buffer} stroke="white" strokeOpacity={0.5} strokeWidth={1} strokeDasharray="2 3" />
              {overdraftLimitEur !== undefined && (
                <ReferenceLine y={-overdraftLimitEur} stroke={CORAL} strokeOpacity={0.9} strokeWidth={1.25} strokeDasharray="5 3" />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>
      <p className="mt-1 text-[11px] text-on-hero/55 sm:hidden" aria-hidden="true">
        Scroll to see all 13 weeks →
      </p>
      {caption && <figcaption className="mt-2 text-[11px] text-on-hero/55">{caption}</figcaption>}
    </figure>
  );
}

// ───────────────────────── Daily forecast (Overview) ─────────────────────────
/*
  One point per day for the 13 weeks. Marks, and only these:
  - Likely range: cream tint between lowEur and highEur (forecast days only).
  - Overdraft:    red tint from the balance up to zero, only where the balance is below zero.
  - Balance:      2px cream line, linear (bank balances step, so it is not smoothed), no dots.
  - Zero line, dashed sand safety buffer, a "Today" marker and the lowest-point callout.
*/
const CREAM = "#f5f1e8";
const RANGE_FILL = "rgb(245 241 232 / 0.10)";
const OVERDRAFT_FILL = "rgb(230 103 103 / 0.45)";
const LOW_RED = "#e66767";
const LIGHT_RED = "#f19a9a";
const BUFFER_SAND = "#e3c584";
const CARD = "#1c2748";
const GRID = "rgb(255 255 255 / 0.07)";
const ZERO = "rgb(255 255 255 / 0.35)";

/** `actual`: before the demo date, so a booked balance, not a forecast. */
type DayRow = ForecastDay & { range?: [number, number]; below: number; actual: boolean };

const weekday = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });

/** "between −€19.8k and −€24.4k": the end nearer to zero first. */
const rangeText = (low: number, high: number) => {
  const [a, b] = Math.abs(high) <= Math.abs(low) ? [high, low] : [low, high];
  return `between ${formatEurK1(a)} and ${formatEurK1(b)}`;
};

function DayTooltip({ active, payload }: TooltipContentProps<number, string>) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload as DayRow;
  return (
    <div className="liquid-glass-strong min-w-52 rounded-xl p-3 text-xs text-ink">
      <p className="font-semibold">
        {weekday(d.date)} {formatDate(d.date)}
        {d.actual && <span className="ml-1.5 font-medium text-ink-soft">(actual)</span>}
      </p>
      <p className={`num mt-1 text-[15px] font-semibold ${d.balanceEur < 0 ? "text-short" : "text-ink"}`}>{formatEurWhole(d.balanceEur)}</p>
      {d.lowEur !== undefined && d.highEur !== undefined && (
        <p className="num mt-0.5 text-ink-soft">Likely {rangeText(d.lowEur, d.highEur)}</p>
      )}
    </div>
  );
}

const swatch = (style: CSSProperties) => <span className="h-2.5 w-2.5 rounded-[3px]" style={style} aria-hidden="true" />;

/** Legend for the daily forecast. */
export function ForecastLegend({ bufferEur, range = true }: { bufferEur?: number; range?: boolean }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[13px] text-on-hero/80">
      <li className="inline-flex items-center gap-1.5">{lineIcon(CREAM, false, 2)}Balance</li>
      {range && (
        <li className="inline-flex items-center gap-1.5">
          {swatch({ background: RANGE_FILL, boxShadow: "inset 0 0 0 1px rgb(245 241 232 / 0.3)" })}Likely range
        </li>
      )}
      <li className="inline-flex items-center gap-1.5">{swatch({ background: OVERDRAFT_FILL })}Overdraft</li>
      <li className="inline-flex items-center gap-1.5">
        {lineIcon(BUFFER_SAND, true, 1.5)}
        Buffer{bufferEur !== undefined && <span className="num font-semibold text-on-hero">&nbsp;{formatEurWhole(bufferEur)}</span>}
      </li>
    </ul>
  );
}

interface DailyProps {
  days: ForecastDay[];
  /** The 13 weeks, for the W1–W13 axis labels. */
  weeks: ForecastWeek[];
  bufferEur: number;
  /** The demo date: where the "Today" marker sits. */
  today: string;
  lowest: LowestPoint;
  /** Fixed height in px. If omitted: 300px on mobile, 380px from sm up. */
  height?: number;
}

/** 13-week cash forecast, one point per day. */
export function CashChart({ days, weeks, bufferEur, today, lowest, height }: DailyProps) {
  const data: DayRow[] = days.map((d) => ({
    ...d,
    range: d.lowEur !== undefined && d.highEur !== undefined ? [d.lowEur, d.highEur] : undefined,
    below: Math.min(d.balanceEur, 0),
    actual: d.date < today,
  }));
  const todayRow = data.find((d) => d.date === today) ?? data[0];

  // One tick per week, in the middle of the week (day 4 of 7). Never skipped.
  const tickDates = weeks.map((_, i) => data[Math.min(i * 7 + 3, data.length - 1)]?.date).filter((d): d is string => !!d);
  const weekByTick = new Map(tickDates.map((d, i) => [d, weeks[i]]));

  const values = data.flatMap((d) => [d.balanceEur, d.lowEur ?? d.balanceEur, d.highEur ?? d.balanceEur]);
  const span = Math.max(...values, bufferEur, 0) - Math.min(...values, 0);
  // Leave room under the lowest point for its callout.
  const ticks = niceTicks([...values, bufferEur, lowest.value - span * 0.2], 6);
  const yMin = ticks[0];
  const yMax = ticks[ticks.length - 1];
  const negDays = days.filter((d) => d.balanceEur < 0).length;

  const XTick = ({ x, y, payload }: { x?: number | string; y?: number | string; payload?: { value: string } }) => {
    const w = payload ? weekByTick.get(payload.value) : undefined;
    if (!w) return <g />;
    const isLow = w.week === lowest.week;
    const top = Number(y ?? 0);
    return (
      <g>
        <text x={x} y={top + 13} textAnchor="middle" fontSize={13} fontWeight={500} fill={isLow ? LIGHT_RED : "#ffffff"}>
          W{w.week}
        </text>
        <text x={x} y={top + 28} textAnchor="middle" fontSize={12} fill={isLow ? LIGHT_RED : WHITE_DIM}>
          {formatDate(w.startDate)}
        </text>
      </g>
    );
  };

  /** Red dot on the lowest point, with a white callout below it. */
  const LowestShape = ({ cx, cy }: { cx?: number; cy?: number }) => {
    if (cx === undefined || cy === undefined) return <g />;
    const w = 118;
    const top = cy + 14;
    return (
      <g pointerEvents="none">
        <circle cx={cx} cy={cy} r={5} fill={LOW_RED} stroke={CARD} strokeWidth={2} />
        <g style={{ filter: "drop-shadow(0 4px 10px rgba(0,0,0,0.25))" }}>
          <rect x={cx - w / 2} y={top} width={w} height={38} rx={10} fill="#ffffff" />
          <path d={`M${cx - 6},${top} L${cx},${top - 6} L${cx + 6},${top} Z`} fill="#ffffff" />
          <text x={cx} y={top + 17} textAnchor="middle" fontSize={13} fontWeight={700} fill={LABEL_SHORT}>
            {formatEurWhole(lowest.value)}
          </text>
          <text x={cx} y={top + 31} textAnchor="middle" fontSize={11} fill={LABEL_MUTED}>
            Lowest, {formatDate(lowest.date)}
          </text>
        </g>
      </g>
    );
  };

  return (
    <figure>
      <p className="sr-only">
        {`Daily cash forecast for ${days.length} days from ${formatDate(days[0]?.date ?? "")}. Safety buffer ${formatEurWhole(bufferEur)}. `}
        {`Lowest ${formatEurWhole(lowest.value)} on ${formatDate(lowest.date)}. `}
        {negDays === 0 ? "The balance stays above zero." : `The balance is below zero on ${negDays} days.`}
        {" Focus the chart and use the arrow keys to move day by day."}
      </p>
      <div className="-mx-1 overflow-x-auto pb-1 sm:mx-0 sm:overflow-visible">
        <div className={`min-w-[720px] sm:min-w-0 ${height ? "" : "h-[300px] sm:h-[380px]"}`} style={height ? { height } : undefined}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 28, right: 12, left: 0, bottom: 4 }} accessibilityLayer>
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="date" ticks={tickDates} tick={XTick} tickLine={false} axisLine={false} interval={0} height={38} />
              <YAxis
                ticks={ticks}
                domain={[yMin, yMax]}
                tickFormatter={formatEurShort}
                tick={{ fill: WHITE_SOFT, fontSize: 13 }}
                tickLine={false}
                axisLine={false}
                width={58}
              />
              <Tooltip
                cursor={{ stroke: "rgb(255 255 255 / 0.4)", strokeWidth: 1 }}
                content={(p) => <DayTooltip {...(p as TooltipContentProps<number, string>)} />}
              />
              <Area dataKey="range" type="linear" fill={RANGE_FILL} fillOpacity={1} stroke="none" activeDot={false} isAnimationActive={false} />
              <Area dataKey="below" type="linear" baseValue={0} fill={OVERDRAFT_FILL} fillOpacity={1} stroke="none" activeDot={false} isAnimationActive={false} />
              <ReferenceLine y={0} stroke={ZERO} strokeWidth={1} />
              <ReferenceLine y={bufferEur} stroke={BUFFER_SAND} strokeWidth={1.5} strokeDasharray="5 5" />
              {todayRow && (
                <ReferenceLine
                  x={todayRow.date}
                  ifOverflow="visible"
                  stroke="rgb(255 255 255 / 0.45)"
                  strokeWidth={1}
                  label={{ value: "Today", position: "insideTopLeft", offset: 6, fill: "#ffffff", fontSize: 12, fontWeight: 600 }}
                />
              )}
              <Line
                dataKey="balanceEur"
                type="linear"
                stroke={CREAM}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
                dot={false}
                activeDot={{ r: 4, fill: CREAM, stroke: CARD, strokeWidth: 2 }}
                isAnimationActive={false}
              />
              {todayRow && <ReferenceDot x={todayRow.date} y={todayRow.balanceEur} r={5} fill={CREAM} stroke={CARD} strokeWidth={2} ifOverflow="visible" />}
              <ReferenceDot x={lowest.date} y={lowest.value} r={5} shape={LowestShape} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>
      <p className="mt-1 text-[11px] text-on-hero/55 sm:hidden" aria-hidden="true">
        Scroll to see all 13 weeks →
      </p>
    </figure>
  );
}
