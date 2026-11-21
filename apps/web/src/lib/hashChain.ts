import type { AuditEntry } from "../api/types";

export const GENESIS_HASH = "0".repeat(64);

export async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** Must match the backend exactly (see AuditEntry docs in types.ts). */
export function hashInput(e: Omit<AuditEntry, "hash">): string {
  return `${e.prevHash}|${e.seq}|${e.timestamp}|${e.actor}|${e.action}|${e.decisionId ?? ""}`;
}

export type ChainResult = { valid: true } | { valid: false; brokenAtSeq: number };

/** Recomputes every hash in the browser to prove nothing was edited. */
export async function verifyChain(entries: AuditEntry[]): Promise<ChainResult> {
  const sorted = [...entries].sort((a, b) => a.seq - b.seq);
  let prev = GENESIS_HASH;
  for (const e of sorted) {
    if (e.prevHash !== prev) return { valid: false, brokenAtSeq: e.seq };
    const expected = await sha256Hex(hashInput(e));
    if (expected !== e.hash) return { valid: false, brokenAtSeq: e.seq };
    prev = e.hash;
  }
  return { valid: true };
}
