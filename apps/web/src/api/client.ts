import type {
  AuditLog,
  ChatRequest,
  ChatResponse,
  Company,
  ContractCheckResult,
  CostCheck,
  CostMemoryRow,
  Decision,
  DecisionAction,
  DemoScenario,
  Digest,
  DocumentRun,
  Finding,
  Forecast,
  KnownDocument,
  Policy,
  SourceDoc,
} from "./types";
import type {
  BeAuditEntry,
  BeCompany,
  BeCostItem,
  BeDecisionResult,
  BeDigest,
  BeDocument,
  BeDocumentSummary,
  BeFinding,
  BeForecast,
  BeMandate,
  BeOverview,
  BeRecommendation,
  BeRun,
  BeRunAccepted,
  BeUploadAccepted,
} from "./backend";
import {
  toAuditEntry,
  toCompany,
  mockToCostCheck,
  toCostCheck,
  toCostMemoryRow,
  toDecision,
  toDecisions,
  toDigest,
  toDocumentRun,
  toFinding,
  toForecast,
  toKnownDocument,
  toPolicy,
  toSourceDoc,
} from "./adapters";
import { verifyBackendChain, verifyMockChain } from "../lib/hashChain";
/** The in-browser mock backend, loaded only in mock mode (it bundles the sample bank statement). */
const mock = () => import("./mock");

export const API_URL: string = import.meta.env.VITE_API_URL ?? "/api/v1";
export const USE_MOCK = import.meta.env.VITE_MOCK === "1";

/** An error from the backend, with the HTTP status, the problem-details text and its code when there is one. */
export class ApiError extends Error {
  readonly status: number;
  readonly path: string;
  /** e.g. RUN_IN_PROGRESS, DOCUMENT_ALREADY_PROCESSED, FILE_TOO_LARGE */
  readonly code?: string;
  constructor(status: number, path: string, message: string, code?: string) {
    super(message);
    this.status = status;
    this.path = path;
    this.code = code;
  }
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_URL + path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail = text;
    let code: string | undefined;
    try {
      const body = JSON.parse(text) as { detail?: unknown; title?: unknown; code?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (typeof body.title === "string") detail = body.title;
      if (typeof body.code === "string") code = body.code;
    } catch {
      // not JSON: keep the raw text
    }
    throw new ApiError(res.status, path, `${res.status} ${path} ${detail}`.trim(), code);
  }
  return res.json() as Promise<T>;
}

const postJson = <T>(path: string, data: unknown) =>
  http<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

/** UUID v4. crypto.randomUUID only exists on HTTPS and localhost, so build one by hand elsewhere. */
function uuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** A state-changing call to the real backend: every one needs a fresh Idempotency-Key (a UUID). */
const command = <T>(path: string, data?: unknown) =>
  http<T>(path, {
    method: "POST",
    headers: {
      "Idempotency-Key": uuid(),
      ...(data === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: data === undefined ? undefined : JSON.stringify(data),
  });

// ───────────────────────── Live mode (services/api), mapped by adapters.ts ─────────────────────────

const live = {
  company: async (): Promise<Company> => {
    const [c, m] = await Promise.all([http<BeCompany>("/company"), http<BeMandate>("/mandate")]);
    return toCompany(c, m);
  },
  policy: async (): Promise<Policy> => toPolicy(await http<BeMandate>("/mandate")),
  decisions: async (): Promise<Decision[]> => toDecisions(await http<BeOverview>("/overview")),
  forecast: async (): Promise<Forecast> =>
    toForecast(await http<BeForecast>("/forecast?weekly=true"), new Date().toISOString()),
  act: async (id: string, action: DecisionAction): Promise<Decision> =>
    toDecision((await command<BeDecisionResult>(`/recommendations/${encodeURIComponent(id)}/${action}`)).recommendation),
  demo: async (scenario: DemoScenario): Promise<{ ok: true }> => {
    if (scenario === "reset") {
      await http("/demo/reset", { method: "POST" });
    } else if (scenario === "closure") {
      const company = await http<BeCompany>("/company");
      await command<BeRunAccepted>("/events", {
        type: "CLOSURE_PLANNED",
        expected_financial_version: company.financial_version,
        payload: { date_from: "2025-06-01", date_to: "2025-06-30", variable_costs_stop: true, note: "Summer closure" },
      });
    } else {
      throw new Error(`The "${scenario}" demo step only exists in mock mode.`);
    }
    return { ok: true };
  },
  latestRun: async (): Promise<BeRun | null> => (await http<BeRun[]>("/runs?limit=1"))[0] ?? null,

  /** Findings with their recommendations, and for an upload its run's trace ("How Vero found out"). */
  findings: async (): Promise<Finding[]> => {
    const [findings, recs] = await Promise.all([http<BeFinding[]>("/findings"), http<BeRecommendation[]>("/recommendations")]);
    const byId = new Map(recs.map((r) => [r.id, r]));
    const runIds = [...new Set(findings.filter((f) => !f.quiet && f.run_id).map((f) => f.run_id!))];
    const runs = new Map(
      await Promise.all(runIds.map(async (id) => [id, await http<BeRun>(`/runs/${id}`).catch(() => null)] as const)),
    );
    return findings.map((f) => toFinding(f, byId, f.run_id ? runs.get(f.run_id) : null));
  },
  digest: async (): Promise<Digest> => {
    const [digest, findings] = await Promise.all([http<BeDigest>("/digest"), http<BeFinding[]>("/findings")]);
    return toDigest(digest, findings);
  },
  /** POST /documents: a PDF of at most 5 MB, answered 202; the check runs in GET /runs/{run_id}. */
  uploadDocument: async (file: File): Promise<DocumentRun> => {
    const body = new FormData();
    body.append("file", file);
    const r = await http<BeUploadAccepted>("/documents", { method: "POST", headers: { "Idempotency-Key": uuid() }, body });
    return { ...r, steps: [], finding_ids: [], checked: false };
  },
  documentRun: async (runId: string): Promise<DocumentRun> => toDocumentRun(await http<BeRun>(`/runs/${encodeURIComponent(runId)}`)),
  /** Each of a finding's actions is a recommendation; the finding is RESOLVED once all are decided. */
  decideFinding: async (approve: string[], reject: string[]): Promise<void> => {
    for (const id of approve) await command(`/recommendations/${encodeURIComponent(id)}/approve`);
    for (const id of reject) await command(`/recommendations/${encodeURIComponent(id)}/reject`);
  },
  acknowledgeFinding: async (id: string): Promise<void> => {
    await command(`/findings/${encodeURIComponent(id)}/acknowledge`);
  },
};

const mockChatAnswer: ChatResponse = {
  answer: "Questions need the real AI service. Start the app without VITE_MOCK=1 to chat with Vero.",
  source: "FALLBACK",
  tools_used: [],
  fallback_reason: "mock mode",
};

/** Single entry point for all data. Flip VITE_MOCK to switch backends. */
export const api = {
  company: (): Promise<Company> => (USE_MOCK ? mock().then((m) => m.getCompany()) : live.company()),
  policy: (): Promise<Policy> => (USE_MOCK ? mock().then((m) => m.getPolicy()) : live.policy()),
  decisions: (): Promise<Decision[]> => (USE_MOCK ? mock().then((m) => m.getDecisions()) : live.decisions()),
  forecast: (): Promise<Forecast> => (USE_MOCK ? mock().then((m) => m.getForecast()) : live.forecast()),
  /** The activity log, its hash chain verified on the raw data before it is converted for display. */
  audit: async (): Promise<AuditLog> => {
    if (USE_MOCK) {
      const entries = await mock().then((m) => m.getAudit());
      return { entries, chain: await verifyMockChain(entries) };
    }
    const raw = await http<BeAuditEntry[]>("/audit");
    const chain = await verifyBackendChain(raw);
    return { entries: raw.map(toAuditEntry), chain };
  },

  act: (id: string, action: DecisionAction): Promise<Decision> =>
    USE_MOCK ? mock().then((m) => m.actOnDecision(id, action)) : live.act(id, action),

  /** Mock only: test a customer or supplier contract on the forecast (the backend checks bills, not contracts). */
  uploadContract: (file: File): Promise<ContractCheckResult> =>
    USE_MOCK ? mock().then((m) => m.uploadContract(file)) : Promise.reject(new Error("Contract simulation only exists in mock mode.")),

  // Vero: cost check. Bills reach it by themselves (bank feed, e-mail) or by upload.
  findings: (): Promise<Finding[]> => (USE_MOCK ? mock().then((m) => m.getFindings()) : live.findings()),
  /** Checked cost changes, summarised for the Overview (real GET /findings, or the mock's findings). */
  costChecks: async (): Promise<CostCheck[]> =>
    USE_MOCK
      ? (await mock().then((m) => m.getFindings())).map(mockToCostCheck)
      : (await http<BeFinding[]>("/findings")).map(toCostCheck),
  costMemory: async (): Promise<CostMemoryRow[]> =>
    USE_MOCK ? mock().then((m) => m.getCostMemory()) : (await http<BeCostItem[]>("/cost-memory")).map(toCostMemoryRow),
  costDocuments: async (): Promise<KnownDocument[]> =>
    USE_MOCK ? mock().then((m) => m.getCostDocuments()) : (await http<BeDocumentSummary[]>("/documents")).map(toKnownDocument),
  digest: (): Promise<Digest> => (USE_MOCK ? mock().then((m) => m.getDigest()) : live.digest()),
  document: async (docId: string): Promise<SourceDoc> =>
    USE_MOCK ? mock().then((m) => m.getDocument(docId)) : toSourceDoc(await http<BeDocument>(`/documents/${encodeURIComponent(docId)}`)),
  /** Upload a bill or notice. Only bills are checked: the rent adjustment and invoices. */
  uploadDocument: (file: File): Promise<DocumentRun> => (USE_MOCK ? mock().then((m) => m.uploadDocument(file)) : live.uploadDocument(file)),
  documentRun: (runId: string): Promise<DocumentRun> => (USE_MOCK ? mock().then((m) => m.getDocumentRun(runId)) : live.documentRun(runId)),
  /** Approve some of a finding's prepared actions and decline the rest. */
  decideFinding: (findingId: string, approve: string[], reject: string[]): Promise<void> =>
    USE_MOCK ? mock().then((m) => m.decideFinding(findingId, approve, reject)) : live.decideFinding(approve, reject),
  /** A change Vero handled quietly: one tap clears it from the digest. */
  acknowledgeFinding: (findingId: string): Promise<void> =>
    USE_MOCK ? mock().then((m) => m.acknowledgeFinding(findingId)) : live.acknowledgeFinding(findingId),

  demo: (scenario: DemoScenario): Promise<{ ok: true }> =>
    USE_MOCK ? mock().then((m) => m.runDemo(scenario)) : live.demo(scenario),

  /** Owner questions, answered by the AI service with read-only tools (nginx: /api/v1/chat → ai/v1/chat). */
  chat: (req: ChatRequest): Promise<ChatResponse> => (USE_MOCK ? Promise.resolve(mockChatAnswer) : postJson("/chat", req)),

  /** Live mode only: the latest agent run, polled instead of an event stream. */
  latestRun: live.latestRun,
};
