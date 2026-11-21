import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { API_URL, USE_MOCK } from "./client";
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

    const es = new EventSource(`${API_URL}/events`);
    es.onopen = () => setState((s) => ({ ...s, connection: "live" }));
    es.onerror = () => setState((s) => ({ ...s, connection: "offline" })); // browser retries
    es.onmessage = (msg) => {
      try {
        handle(JSON.parse(msg.data) as ServerEvent);
      } catch (err) {
        console.warn("Bad SSE message", msg.data, err);
      }
    };
    return () => es.close();
  }, [qc]);

  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}
