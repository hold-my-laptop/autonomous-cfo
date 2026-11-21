import { useId, useState } from "react";
import type { Decision, DecisionAction } from "../../api/types";
import { useDecisionAction } from "../../api/queries";
import { useLive } from "../../api/live";
import { AutonomyBadge, ExecutionBadge } from "../../components/Badges";
import { useToast } from "../../components/Toast";
import { agentName } from "../../lib/labels";
import { DisclosureButton, EmailPanel, ReasoningPanel } from "./Reasoning";

const toneClass = { neutral: "text-ink", good: "text-safe", bad: "text-short" } as const;

export const btnPrimary =
  "inline-flex h-10 items-center justify-center rounded-full btn-primary px-5 text-[14px] font-semibold disabled:opacity-60";
export const btnQuiet =
  "inline-flex h-10 items-center justify-center rounded-full px-5 text-[14px] font-semibold text-ink-soft hover:glass-inset hover:text-ink disabled:opacity-60";

/** Approve / reject with toasts. Shared by the card and the hero summary. */
export function useDecide(d: Decision) {
  const action = useDecisionAction();
  const toast = useToast();
  const act = async (a: DecisionAction) => {
    try {
      await action.mutateAsync({ id: d.id, action: a });
      const msg = a === "approve" ? d.approvedMessage : d.rejectedMessage;
      if (msg) toast(msg, a === "approve" ? "done" : "neutral");
    } catch {
      toast("That didn't go through. Check the connection and try again.", "neutral");
    }
  };
  return { act, pending: action.isPending, approving: action.isPending && action.variables?.action === "approve" };
}

export const cardId = (id: string) => `card-${id}`;

/** A card for something the owner can act on: "Needs your OK" or "Suggestion". */
export function DecisionCard({ decision: d }: { decision: Decision }) {
  const { act, pending, approving } = useDecide(d);
  const { arrived } = useLive();
  const [panel, setPanel] = useState<"reasoning" | "email" | null>(null);
  const panelId = useId();

  const toggle = (p: "reasoning" | "email") => setPanel((cur) => (cur === p ? null : p));
  const facts = d.facts.slice(0, 3);

  return (
    <article
      id={cardId(d.id)}
      tabIndex={-1}
      aria-labelledby={`h-${d.id}`}
      className={`${arrived.has(d.id) ? "arrive" : ""} liquid-glass-strong scroll-mt-32 overflow-hidden rounded-3xl sm:scroll-mt-24 `}
    >
      <div className="px-5 pt-5 sm:px-6">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-auto text-xs font-medium text-ink-soft">{agentName[d.agent]}</span>
          <AutonomyBadge level={d.autonomy} />
          <ExecutionBadge mode={d.execution} />
        </div>

        <h3 id={`h-${d.id}`} className="mt-2 text-[20px] font-semibold leading-snug">
          {d.headline}
        </h3>
        <p className="mt-1 max-w-[75ch] text-sm text-ink-soft">
          {d.summary}
          {d.offer && (
            <span className="text-ink">
              {" "}
              {d.offer.name},{" "}
              {d.offer.kind === "fx_forward"
                ? `${d.offer.ratePct.toLocaleString("en-GB")} EUR/CHF.`
                : `${d.offer.ratePct.toLocaleString("en-GB")}% a year.`}
            </span>
          )}
        </p>
      </div>

      {facts.length > 0 && (
        <dl
          className="glass-inset mx-5 mt-4 grid rounded-xl sm:mx-6"
          style={{ gridTemplateColumns: `repeat(${facts.length}, minmax(0, 1fr))` }}
        >
          {facts.map((f) => (
            <div key={f.label} className="border-l border-line px-3 py-3 first:border-l-0 sm:px-4">
              <dt className="text-xs leading-tight text-ink-soft">{f.label}</dt>
              <dd className={`num tight mt-1 text-base font-semibold ${toneClass[f.tone ?? "neutral"]}`}>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:px-6">
        <div className="-ml-1 flex flex-wrap items-center gap-x-4">
          {d.reasoning.length > 0 && (
            <DisclosureButton open={panel === "reasoning"} onToggle={() => toggle("reasoning")} controls={panelId}>
              How did you decide?
            </DisclosureButton>
          )}
          {d.draftMessage && (
            <DisclosureButton open={panel === "email"} onToggle={() => toggle("email")} controls={panelId} icon="mail">
              Read the draft I wrote
            </DisclosureButton>
          )}
        </div>

        {d.status === "pending" && (
          <div className="flex gap-2 sm:ml-auto">
            <button type="button" disabled={pending} onClick={() => act("reject")} className={`${btnQuiet} flex-1 sm:flex-none`}>
              {d.rejectLabel ?? "Not now"}
            </button>
            <button type="button" disabled={pending} onClick={() => act("approve")} className={`${btnPrimary} flex-1 sm:flex-none`}>
              {approving ? "Working…" : d.approveLabel ?? "Approve"}
            </button>
          </div>
        )}
      </div>

      {panel && (
        <div className="px-5 pb-5 sm:px-6">
          {panel === "reasoning" ? <ReasoningPanel decision={d} id={panelId} /> : <EmailPanel decision={d} id={panelId} />}
        </div>
      )}
    </article>
  );
}
