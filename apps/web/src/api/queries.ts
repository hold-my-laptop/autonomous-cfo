import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { Decision, DecisionAction, Finding, RecommendedAction } from "./types";

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
      qc.invalidateQueries({ queryKey: keys.audit });
    },
  });
}

export function useApproveFinding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, actions }: { id: string; actions: RecommendedAction["type"][] }) => api.approveFinding(id, actions),
    onSuccess: (updated) => {
      qc.setQueryData<Finding[]>(keys.findings, (old = []) => [updated, ...old.filter((f) => f.finding_id !== updated.finding_id)]);
      qc.invalidateQueries({ queryKey: keys.audit });
    },
  });
}

export function useContractUpload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadContract(file),
    // A new document can restart or finish a cost check.
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.costs }),
  });
}
