import type {
  AuditEntry,
  Company,
  ContractCheckResult,
  CostMemoryRow,
  Decision,
  DecisionAction,
  DemoScenario,
  DigestRow,
  Finding,
  Forecast,
  KnownDocument,
  Policy,
  RecommendedAction,
  SourceDoc,
} from "./types";
/** The in-browser mock backend, loaded only in mock mode (it bundles the sample bank statement). */
const mock = () => import("./mock");

export const API_URL: string = import.meta.env.VITE_API_URL ?? "/api/v1";
export const USE_MOCK = import.meta.env.VITE_MOCK === "1";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_URL + path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${path} ${text}`);
  }
  return res.json() as Promise<T>;
}

function upload<T>(path: string, file: File): Promise<T> {
  const body = new FormData();
  body.append("file", file);
  return http<T>(path, { method: "POST", body });
}

const postJson = <T>(path: string, data: unknown) =>
  http<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

/** Single entry point for all data. Flip VITE_MOCK to switch backends. */
export const api = {
  company: (): Promise<Company> => (USE_MOCK ? mock().then((m) => m.getCompany()) : http("/company")),
  policy: (): Promise<Policy> => (USE_MOCK ? mock().then((m) => m.getPolicy()) : http("/policy")),
  decisions: (): Promise<Decision[]> => (USE_MOCK ? mock().then((m) => m.getDecisions()) : http("/decisions")),
  forecast: (): Promise<Forecast> => (USE_MOCK ? mock().then((m) => m.getForecast()) : http("/forecast")),
  audit: (): Promise<AuditEntry[]> => (USE_MOCK ? mock().then((m) => m.getAudit()) : http("/audit")),

  act: (id: string, action: DecisionAction): Promise<Decision> =>
    USE_MOCK ? mock().then((m) => m.actOnDecision(id, action)) : http(`/decisions/${id}/${action}`, { method: "POST" }),

  uploadContract: (file: File): Promise<ContractCheckResult> =>
    USE_MOCK ? mock().then((m) => m.uploadContract(file)) : upload("/contracts", file),

  // Vero: cost check. Documents arrive by themselves (bank feed, e-mail, Contract check); no upload here.
  findings: (): Promise<Finding[]> => (USE_MOCK ? mock().then((m) => m.getFindings()) : http("/costs/findings")),
  costMemory: (): Promise<CostMemoryRow[]> => (USE_MOCK ? mock().then((m) => m.getCostMemory()) : http("/costs/memory")),
  costDocuments: (): Promise<KnownDocument[]> => (USE_MOCK ? mock().then((m) => m.getCostDocuments()) : http("/costs/documents")),
  digest: (): Promise<DigestRow[]> => (USE_MOCK ? mock().then((m) => m.getDigest()) : http("/costs/digest")),
  document: (docId: string): Promise<SourceDoc> =>
    USE_MOCK ? mock().then((m) => m.getDocument(docId)) : http(`/documents/${encodeURIComponent(docId)}`),
  approveFinding: (findingId: string, actions: RecommendedAction["type"][]): Promise<Finding> =>
    USE_MOCK ? mock().then((m) => m.approveFinding(findingId, actions)) : postJson(`/costs/findings/${findingId}/approve`, { actions }),

  demo: (scenario: DemoScenario): Promise<{ ok: true }> =>
    USE_MOCK ? mock().then((m) => m.runDemo(scenario)) : http(`/demo/${scenario}`, { method: "POST" }),
};
