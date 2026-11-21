import type { BeAuditEntry } from "../api/backend";
import type { AuditEntry } from "../api/types";

export const GENESIS_HASH = "0".repeat(64);

export async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export type ChainResult = { valid: true } | { valid: false; brokenAtSeq: number };

/**
 * The real backend's sealed text (DATA_CONTRACTS §2.2, AuditEntry; services/api audit.hash_input):
 * prev_hash|seq|timestamp|actor|agent|run_id|recommendation_id|lane|execution|model|action,
 * absent values as empty text. Every field is covered, so a changed lane or model breaks the chain.
 */
export function backendHashInput(e: BeAuditEntry): string {
  return [
    e.prev_hash,
    String(e.seq),
    e.timestamp,
    e.actor,
    e.agent ?? "",
    e.run_id ?? "",
    e.recommendation_id ?? "",
    e.lane ?? "",
    e.execution ?? "",
    e.model ?? "",
    e.action,
  ].join("|");
}

/** The in-browser mock's sealed text (6 fields; see api/mock/runtime.ts). */
export function mockHashInput(e: Omit<AuditEntry, "hash">): string {
  return `${e.prevHash}|${e.seq}|${e.timestamp}|${e.actor}|${e.action}|${e.decisionId ?? ""}`;
}

/**
 * Verifies the raw response from GET /audit, before anything is converted for display:
 * seq runs 1, 2, 3 … without gaps, each prev_hash is the previous hash (64 zeros first),
 * and each hash is the SHA-256 of its sealed text.
 */
export async function verifyBackendChain(entries: BeAuditEntry[]): Promise<ChainResult> {
  const sorted = [...entries].sort((a, b) => a.seq - b.seq);
  let prev = GENESIS_HASH;
  for (const [i, e] of sorted.entries()) {
    if (e.seq !== i + 1 || e.prev_hash !== prev) return { valid: false, brokenAtSeq: e.seq };
    if ((await sha256Hex(backendHashInput(e))) !== e.hash) return { valid: false, brokenAtSeq: e.seq };
    prev = e.hash;
  }
  return { valid: true };
}

/** The same check for the mock's entries. */
export async function verifyMockChain(entries: AuditEntry[]): Promise<ChainResult> {
  const sorted = [...entries].sort((a, b) => a.seq - b.seq);
  let prev = GENESIS_HASH;
  for (const e of sorted) {
    if (e.prevHash !== prev) return { valid: false, brokenAtSeq: e.seq };
    if ((await sha256Hex(mockHashInput(e))) !== e.hash) return { valid: false, brokenAtSeq: e.seq };
    prev = e.hash;
  }
  return { valid: true };
}
