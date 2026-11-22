import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { ChatTurn, Decision, DecisionAction } from "./types";

export const keys = {
  company: ["company"] as const,
  policy: ["policy"] as const,
  decisions: ["decisions"] as const,
  forecast: ["forecast"] as const,
  audit: ["audit"] as const,
  costs: ["costs"] as const,
  findings: ["costs", "findings"] as const,
  costMemory: ["costs", "memory"] as const,
  costDocuments: ["costs", "documents"] as const,
  digest: ["costs", "digest"] as const,
  document: (id: string) => ["documents", id] as const,
};

export const useCompany = () => useQuery({ queryKey: keys.company, queryFn: api.company });
export const usePolicy = () => useQuery({ queryKey: keys.policy, queryFn: api.policy });
export const useDecisions = () => useQuery({ queryKey: keys.decisions, queryFn: api.decisions });
export const useForecast = () => useQuery({ queryKey: keys.forecast, queryFn: api.forecast });
export const useAudit = () => useQuery({ queryKey: keys.audit, queryFn: api.audit });

export const useFindings = () => useQuery({ queryKey: keys.findings, queryFn: api.findings });
export const useCostMemory = () => useQuery({ queryKey: keys.costMemory, queryFn: api.costMemory });
/** Checked cost changes for the Overview: what Vero verified, and what needs the owner. */
export const useCostChecks = () => useQuery({ queryKey: [...keys.costs, "checks"] as const, queryFn: api.costChecks });
export const useCostDocuments = () => useQuery({ queryKey: keys.costDocuments, queryFn: api.costDocuments });
export const useDigest = () => useQuery({ queryKey: keys.digest, queryFn: api.digest });
export const useDocument = (id: string | null) =>
  useQuery({ queryKey: keys.document(id ?? ""), queryFn: () => api.document(id!), enabled: !!id, staleTime: Infinity });

export function useDecisionAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: DecisionAction }) => api.act(id, action),
    onSuccess: (updated) => {
      qc.setQueryData<Decision[]>(keys.decisions, (old = []) =>
        old.map((d) => (d.id === updated.id ? updated : d)),
      );
      // An approval changes the schedule (forecast, balance) and can create follow-up items.
      qc.invalidateQueries({ queryKey: keys.decisions });
      qc.invalidateQueries({ queryKey: keys.forecast });
      qc.invalidateQueries({ queryKey: keys.company });
      qc.invalidateQueries({ queryKey: keys.audit });
    },
  });
}

/** One question to Vero. History is the earlier turns, oldest first (the AI service keeps at most 6). */
export function useChat() {
  return useMutation({
    mutationFn: ({ question, history }: { question: string; history: ChatTurn[] }) =>
      api.chat({
        question,
        language: "en",
        history: history.slice(-6).map((t) => ({ ...t, content: t.content.slice(0, 2000) })),
      }),
  });
}

/** Everything a cost decision can change: the checks, the Overview's items, the forecast and the log. */
function refreshAfterCosts(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: keys.costs });
  qc.invalidateQueries({ queryKey: keys.decisions });
  qc.invalidateQueries({ queryKey: keys.forecast });
  qc.invalidateQueries({ queryKey: keys.audit });
}

/** Approve some of a finding's prepared actions and decline the others. */
export function useDecideFinding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, approve, reject }: { id: string; approve: string[]; reject: string[] }) => api.decideFinding(id, approve, reject),
    // Also after a failure: some actions may have gone through before it.
    onSettled: () => refreshAfterCosts(qc),
  });
}

/** One tap on a change Vero handled quietly. */
export function useAcknowledgeFinding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.acknowledgeFinding(id),
    onSettled: () => refreshAfterCosts(qc),
  });
}

/** Upload a bill or notice; follow its run with useDocumentRun. */
export function useUploadDocument() {
  return useMutation({ mutationFn: (file: File) => api.uploadDocument(file) });
}

/** The upload's run, polled until it has finished; then the checks are refreshed. */
export function useDocumentRun(runId: string | null) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["runs", runId ?? ""] as const,
    queryFn: async () => {
      const run = await api.documentRun(runId!);
      if (run.status !== "RUNNING") refreshAfterCosts(qc);
      return run;
    },
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data?.status === "RUNNING" || !q.state.data ? 1000 : false),
    refetchIntervalInBackground: true,
  });
}

/** Mock only: a contract tested on the forecast. */
export function useContractUpload() {
  return useMutation({ mutationFn: (file: File) => api.uploadContract(file) });
}
