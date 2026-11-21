import { useEffect, useState } from "react";
import { useAudit } from "../../api/queries";
import type { AuditActor } from "../../api/types";
import { AutonomyBadge, ExecutionBadge } from "../../components/Badges";
import { Icon } from "../../components/Icon";
import { verifyChain, type ChainResult } from "../../lib/hashChain";
import { agentName } from "../../lib/labels";
import { formatDateTime, shortHash } from "../../lib/format";
import { ModelLine } from "../today/Reasoning";

const actorName: Record<AuditActor, string> = {
  agent: "Agent",
  rules: "Rule check",
  owner: "You",
  system: "System",
};

export function ActivityPage() {
  const { data, isLoading, isError } = useAudit();
  const [chain, setChain] = useState<ChainResult | null>(null);

  useEffect(() => {
    if (data) verifyChain(data).then(setChain);
  }, [data]);

  const entries = data ? [...data].reverse() : [];

  return (
    <div className="max-w-4xl">
      <h1 className="text-[32px] font-semibold leading-[1.1] tracking-[-0.025em] sm:text-[44px] lg:text-[48px]">Activity</h1>
      <p className="mt-2 max-w-[62ch] text-ink-soft">
        Everything your agents and you did, newest first. Each line is sealed to the one before it, so nobody can
        change the history later without it showing.
      </p>

      {chain && (
        <p
          className={`mt-4 inline-flex items-center gap-2 rounded-3xl px-3 py-2 text-[13px] font-medium ${
            chain.valid ? "bg-safe-tint text-safe" : "bg-short-tint text-short"
          }`}
        >
          <Icon name={chain.valid ? "shield" : "alert"} />
          {chain.valid
            ? `History checked: all ${data?.length} entries are unchanged`
            : `History was changed at entry ${chain.brokenAtSeq}`}
        </p>
      )}

      {isLoading && <div className="mt-6 h-48 animate-pulse rounded-3xl bg-line/60" />}
      {isError && <p className="mt-6 text-short">Couldn't load the activity. Check that the backend is running.</p>}

      {data && (
        <ol className="liquid-glass-strong mt-6 divide-y divide-line overflow-hidden rounded-3xl">
          {entries.map((e) => (
            <li key={e.seq} className="grid gap-x-6 gap-y-2 px-5 py-4 sm:grid-cols-[9rem_minmax(0,1fr)]">
              <div className="text-sm text-ink-soft">
                <p>{formatDateTime(e.timestamp)}</p>
                <p className="font-semibold text-ink">
                  {e.actor === "agent" && e.agent ? agentName[e.agent] : actorName[e.actor]}
                </p>
              </div>
              <div>
                <p className="text-[13px]">{e.action}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {e.autonomy && <AutonomyBadge level={e.autonomy} />}
                  {e.execution && <ExecutionBadge mode={e.execution} />}
                  <span className="text-xs text-ink-soft" title={`Seal: ${e.hash}\nPrevious: ${e.prevHash}`}>
                    Seal {shortHash(e.hash)}
                  </span>
                </div>
                {e.model && <ModelLine model={e.model} className="mt-1.5" />}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
