import type { AutonomyLevel, ExecutionMode } from "../api/types";
import { autonomyExplain, autonomyLabel, executionLabel } from "../lib/labels";
import { Icon } from "./Icon";

const base = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-medium leading-5";

const autonomyStyle: Record<AutonomyLevel, { cls: string; dot: string }> = {
  analysis: { cls: "bg-sunk text-ink-soft", dot: "bg-ink-muted" },
  recommendation: { cls: "bg-action-tint text-link", dot: "bg-navy" },
  approval_required: { cls: "bg-cream text-ask", dot: "bg-ask" },
  done: { cls: "bg-safe-tint text-safe", dot: "bg-safe" },
};

export function AutonomyBadge({ level }: { level: AutonomyLevel }) {
  const s = autonomyStyle[level];
  return (
    <span title={autonomyExplain[level]} className={`${base} ${s.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} aria-hidden="true" />
      {autonomyLabel[level]}
    </span>
  );
}

export function OwnerBadge({ approved }: { approved: boolean }) {
  return (
    <span className={`${base} ${approved ? "bg-navy text-on-hero" : "border border-line-strong text-ink"}`}>
      <Icon name={approved ? "check" : "x"} size={12} />
      {approved ? "You approved" : "You said no"}
    </span>
  );
}

export function ExecutionBadge({ mode }: { mode: ExecutionMode }) {
  if (mode === "permitted") {
    return <span className={`${base} border border-short text-short`}>{executionLabel.permitted}</span>;
  }
  return (
    <span
      title="Demo mode: nothing is sent to the bank and no money moves."
      className={`${base} border border-line text-ink-soft`}
    >
      {executionLabel.simulated}
    </span>
  );
}
