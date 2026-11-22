import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { ActionDecisions, ChatTurn, Decision, DecisionAction, UploadRun } from "./types";

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

/** Everything a cost-check decision can change: findings and digest, the Overview's lanes, the forecast and the log. */
function invalidateAfterProof(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: keys.costs });
  qc.invalidateQueries({ queryKey: keys.decisions });
  qc.invalidateQueries({ queryKey: keys.forecast });
  qc.invalidateQueries({ queryKey: keys.company });
  qc.invalidateQueries({ queryKey: keys.audit });
}

/** Approve or reject each prepared action of a finding (live: one recommendation each). */
export function useDecideActions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (decisions: ActionDecisions) => api.decideActions(decisions),
    // Refetch even after a partial failure: some actions may already be decided.
    onSettled: () => invalidateAfterProof(qc),
  });
}

/** The owner's one tap on a change Vero handled quietly. */
export function useAcknowledgeFinding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (findingId: string) => api.acknowledgeFinding(findingId),
    onSettled: () => invalidateAfterProof(qc),
  });
}

/** Upload a bill or notice and follow its check; `onProgress` gets each poll of the run. */
export function useUploadBill(onProgress?: (run: UploadRun) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadBill(file, onProgress),
    onSettled: () => invalidateAfterProof(qc),
  });
}

/** Mock only: test a customer contract on the forecast. */
export function useContractSimulation() {
  return useMutation({ mutationFn: (file: File) => api.simulateContract(file) });
}
