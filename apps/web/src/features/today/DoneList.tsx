import { Fragment, useId, useState } from "react";
import type { Decision } from "../../api/types";
import { useLive } from "../../api/live";
import { AutonomyBadge, ExecutionBadge, OwnerBadge } from "../../components/Badges";
import { Icon } from "../../components/Icon";
import { agentName } from "../../lib/labels";
import { formatDateTime } from "../../lib/format";
import { DraftEmail, ModelLine, Reasoning } from "./Reasoning";

function outcome(d: Decision): string | null {
  if (d.status === "approved") return `You chose "${d.approveLabel ?? "Approve"}"`;
  if (d.status === "rejected") return `You chose "${d.rejectLabel ?? "Not now"}"`;
  return null;
}

function StatusBadges({ d }: { d: Decision }) {
  const decided = d.status === "approved" || d.status === "rejected";
  return (
    <span className="flex flex-wrap gap-1.5">
      {decided ? <OwnerBadge approved={d.status === "approved"} /> : <AutonomyBadge level={d.autonomy} />}
      {d.autonomy !== "analysis" && <ExecutionBadge mode={d.execution} />}
    </span>
  );
}

const th = "px-4 py-2 text-left text-xs font-medium text-ink-soft sm:px-5";

/** Compact table of things already handled: done by the agent, checks, and the owner's decisions. */
export function DoneList({ items }: { items: Decision[] }) {
  return (
    <div className="liquid-glass-strong overflow-hidden rounded-3xl">
      <table className="w-full table-fixed border-collapse">
        <thead className="glass-inset border-b border-line">
          <tr>
            <th scope="col" className={`${th} hidden w-32 sm:table-cell`}>Date</th>
            <th scope="col" className={`${th} hidden w-36 md:table-cell`}>Agent</th>
            <th scope="col" className={th}>Action</th>
            <th scope="col" className={`${th} hidden w-64 sm:table-cell`}>Status</th>
            <th scope="col" className="w-10"><span className="sr-only">Details</span></th>
          </tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <DoneRow key={d.id} d={d} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DoneRow({ d }: { d: Decision }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const { arrived } = useLive();
  const result = outcome(d);
  const when = formatDateTime(d.resolvedAt ?? d.createdAt);
  const toggle = () => setOpen((o) => !o);

  return (
    <Fragment>
      <tr
        onClick={toggle}
        className={`${arrived.has(d.id) ? "arrive" : ""} cursor-pointer border-t border-line align-top first:border-t-0 hover:bg-sunk/50`}
      >
        <td className="num hidden whitespace-nowrap px-4 py-3 text-[13px] text-ink-soft sm:table-cell sm:px-5">{when}</td>
        <td className="hidden truncate px-4 py-3 text-[13px] text-ink-soft md:table-cell md:px-5">{agentName[d.agent]}</td>
        <td className="px-4 py-3 sm:px-5">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={id}
            onClick={(e) => {
              e.stopPropagation();
              toggle();
            }}
            className="block w-full text-left text-[13px] font-medium leading-snug text-ink"
          >
            {d.headline}
          </button>
          <span className="mt-0.5 block text-xs text-ink-soft md:hidden">
            {agentName[d.agent]}
            <span className="sm:hidden"> · {when}</span>
          </span>
          <span className="mt-1.5 block sm:hidden">
            <StatusBadges d={d} />
          </span>
        </td>
        <td className="hidden px-4 py-3 sm:table-cell sm:px-5">
          <StatusBadges d={d} />
        </td>
        <td className="py-3 pr-3 text-right">
          <Icon
            name="chevron"
            size={16}
            className={`inline text-ink-muted transition-transform ${open ? "rotate-180" : ""}`}
          />
        </td>
      </tr>

      {open && (
        <tr id={id} className="bg-sunk/30">
          <td colSpan={5} className="px-4 pb-4 pt-1 sm:px-5">
            <div className="max-w-3xl space-y-2">
              <p className="text-[13px] text-ink-soft">
                {d.summary}
                {result && <span className="font-medium text-ink"> {result}.</span>}
              </p>
              <Reasoning decision={d} label="Show the steps" />
              {d.model && <ModelLine model={d.model} />}
              <DraftEmail decision={d} />
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}
