import { createContext, useContext, useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { announce } from "../lib/announce";
import { api, USE_MOCK } from "./client";
import { mockBus } from "./mockBus";
import { keys } from "./queries";
import type { AgentId, AuditEntry, Decision, Finding, KnownDocument, ServerEvent } from "./types";

export type ConnectionState = "connecting" | "live" | "offline";
type AgentStatus = { message: string; busy: boolean };

interface LiveState {
  connection: ConnectionState;
  latest: AgentStatus & { agent: AgentId | "system" };
  agents: Partial<Record<AgentId, AgentStatus>>;
  /** ids of decisions that arrived live, so the UI can highlight them once */
  arrived: Set<string>;
}

const initial: LiveState = {
  connection: "connecting",
  latest: { agent: "system", message: "Watching your money", busy: false },
  agents: {},
  arrived: new Set(),
};

const LiveContext = createContext<LiveState>(initial);
export const useLive = () => useContext(LiveContext);

const POLL_BUSY_MS = 1500;
const POLL_IDLE_MS = 4000;

/**
 * Real backend: it has no event stream, so poll the latest run (GET /runs?limit=1).
 * While a run works, its newest trace line is the status text. When a run finishes,
 * refresh decisions, forecast and company, and highlight the recommendations it made.
 */
function pollRuns(qc: QueryClient, setState: Dispatch<SetStateAction<LiveState>>): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let seen: string | null = null; // run_id + status from the last poll
  let first = true;

  const tick = async () => {
    let busy = false;
    try {
      const run = await api.latestRun();
      if (stopped) return;
      setState((s) => (s.connection === "live" ? s : { ...s, connection: "live" }));
      if (run) {
        busy = run.status === "QUEUED" || run.status === "RUNNING";
        const message = busy
          ? (run.trace.at(-1)?.label ?? run.trigger.label)
          : run.status === "FAILED"
            ? `Couldn't finish: ${run.trigger.label}`
            : `Last check: ${run.trigger.label}`;
        setState((s) => ({
          ...s,
          latest: { agent: "liquidity", message, busy },
          agents: { ...s.agents, liquidity: { message, busy } },
        }));

        const key = `${run.run_id}:${run.status}`;
        if (!first && key !== seen && !busy) {
          const ids = run.result?.recommendation_ids ?? [];
          announce(
            run.status === "FAILED"
              ? "Vero couldn't finish the check."
              : ids.length
                ? `Vero finished checking: ${ids.length} new ${ids.length === 1 ? "item" : "items"} on the Overview.`
                : "Vero finished checking. Nothing new.",
          );
          setState((s) => ({ ...s, arrived: new Set([...s.arrived, ...ids]) }));
          qc.invalidateQueries({ queryKey: keys.decisions });
          qc.invalidateQueries({ queryKey: keys.forecast });
          qc.invalidateQueries({ queryKey: keys.company });
        }
        seen = key;
      }
      first = false;
    } catch {
      if (!stopped) setState((s) => ({ ...s, connection: "offline" }));
    }
    if (!stopped) timer = setTimeout(tick, busy ? POLL_BUSY_MS : POLL_IDLE_MS);
  };

  tick();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}

/**
 * Subscribes to live updates (SSE in real mode, mockBus in mock mode)
 * and writes them straight into the React Query cache.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>(initial);

  useEffect(() => {
    const handle = (ev: ServerEvent) => {
      switch (ev.type) {
        case "decision.created":
        case "decision.updated":
          if (ev.type === "decision.created") {
            const id = ev.data.id;
            announce(`New item: ${ev.data.headline}`);
            setState((s) => ({ ...s, arrived: new Set(s.arrived).add(id) }));
          }
          qc.setQueryData<Decision[]>(keys.decisions, (old = []) => [
            ev.data,
            ...old.filter((d) => d.id !== ev.data.id),
          ]);
          break;
        case "forecast.updated":
          qc.setQueryData(keys.forecast, ev.data);
          break;
        case "company.updated":
          qc.setQueryData(keys.company, ev.data);
          break;
        case "finding.updated":
          qc.setQueryData<Finding[]>(keys.findings, (old = []) => [
            ev.data,
            ...old.filter((f) => f.finding_id !== ev.data.finding_id),
          ]);
          break;
        case "finding.step": {
          const { finding_id, step } = ev.data;
          qc.setQueryData<Finding[]>(keys.findings, (old = []) =>
            old.map((f) =>
              f.finding_id === finding_id
                ? { ...f, investigation_trace: [...f.investigation_trace.filter((s) => s.step !== step.step), step] }
                : f,
            ),
          );
          break;
        }
        case "document.added":
          qc.setQueryData<KnownDocument[]>(keys.costDocuments, (old = []) => [
            ...old.filter((d) => d.doc_id !== ev.data.doc_id),
            ev.data,
          ]);
          break;
        case "audit.appended":
          qc.setQueryData<AuditEntry[]>(keys.audit, (old) =>
            old ? [...old.filter((e) => e.seq !== ev.data.seq), ev.data] : old,
          );
          break;
        case "agent.status": {
          const { agent, message, busy } = ev.data;
          setState((s) => ({
            ...s,
            latest: { agent, message, busy },
            agents: agent === "system" ? s.agents : { ...s.agents, [agent]: { message, busy } },
          }));
          break;
        }
      }
    };

    if (USE_MOCK) {
      setState((s) => ({ ...s, connection: "live" }));
      return mockBus.subscribe(handle);
    }
    return pollRuns(qc, setState);
  }, [qc]);

  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}
