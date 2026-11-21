/** Shared plumbing for the in-browser mock backend: clock, audit chain, live events, model info. */
import type { AgentId, AuditActor, AuditEntry, ModelInfo } from "../types";
import { mockBus } from "../mockBus";
import { GENESIS_HASH, hashInput, sha256Hex } from "../../lib/hashChain";

export const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
/** The demo time machine: timestamps use the demo date with today's clock time. */
let demoDate = "2025-04-14";
export const setDemoDate = (iso: string) => {
  demoDate = iso;
};
const onDemoDate = (d: Date) => {
  const [y, m, day] = demoDate.split("-").map(Number);
  return new Date(y, m - 1, day, d.getHours(), d.getMinutes(), d.getSeconds()).toISOString();
};
export const nowIso = () => onDemoDate(new Date());
export const minutesAgo = (m: number) => onDemoDate(new Date(Date.now() - m * 60_000));
export const clone = <T>(v: T): T => structuredClone(v);

let idCounter = 100;
export const newId = (prefix: string) => `${prefix}_${++idCounter}`;

/** Short, stable hex id for a mock MLflow trace. */
function traceId(seed: string) {
  let h = 2166136261;
  for (const c of seed) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return (h >>> 0).toString(16).padStart(8, "0").slice(0, 6);
}

/** Which model answered: Granite on vLLM, served by Red Hat OpenShift AI. */
export const model = (seed: string, guardrails: ModelInfo["guardrails"] = "passed"): ModelInfo => ({
  name: "granite-3.3-8b-instruct",
  servedBy: "vLLM on Red Hat OpenShift AI",
  traceId: traceId(seed),
  guardrails,
});

export function status(agent: AgentId | "system", message: string, busy: boolean) {
  mockBus.emit({ type: "agent.status", data: { agent, message, busy } });
}

// ───────────── Audit chain ─────────────

let audit: AuditEntry[] = [];
export const getAuditEntries = () => audit;
export const resetAudit = () => {
  audit = [];
};

/** Appends a hash-chained entry. Entries are written one at a time, in order. */
let chain: Promise<unknown> = Promise.resolve();
export function log(
  actor: AuditActor,
  action: string,
  extra: Pick<AuditEntry, "agent" | "autonomy" | "execution" | "decisionId" | "model"> = {},
): Promise<void> {
  const run = async () => {
    const prevHash = audit.length ? audit[audit.length - 1].hash : GENESIS_HASH;
    const entryBase = { seq: audit.length + 1, timestamp: nowIso(), actor, action, prevHash, ...extra };
    const entry: AuditEntry = { ...entryBase, hash: await sha256Hex(hashInput(entryBase)) };
    audit = [...audit, entry];
    mockBus.emit({ type: "audit.appended", data: entry });
  };
  const next = chain.then(run);
  chain = next.catch(() => undefined);
  return next;
}
