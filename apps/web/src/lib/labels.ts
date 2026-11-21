import type { AgentId, AutonomyLevel, ExecutionMode, ModelInfo } from "../api/types";

export const agentName: Record<AgentId | "system", string> = {
  liquidity: "Cash agent",
  fx: "Currency agent",
  tax: "Tax agent",
  costs: "Vero – Cost check",
  contract: "Contract check",
  system: "Your CFO",
};

export const agentJob: Record<AgentId, string> = {
  liquidity: "Forecasts your cash 13 weeks ahead and closes gaps",
  fx: "Watches your Swiss franc transfers and the exchange rate",
  tax: "Keeps tax prepayments in line with what you really earn",
  costs: "Checks every recurring cost against your contracts",
  contract: "Reads contracts and tests them on your forecast",
};

export const autonomyLabel: Record<AutonomyLevel, string> = {
  analysis: "Checked on its own",
  recommendation: "Suggestion",
  approval_required: "Needs your OK",
  done: "Done for you",
};

export const autonomyExplain: Record<AutonomyLevel, string> = {
  analysis: "The agent only looked at your data. Nothing was changed.",
  recommendation: "An idea you can take or ignore. Nothing happens unless you say yes.",
  approval_required: "Nothing happens until you decide.",
  done: "Done automatically, inside the limits you set.",
};

export const executionLabel: Record<ExecutionMode, string> = {
  simulated: "Simulated",
  permitted: "Real action",
};

/** "granite-3.3-8b-instruct" → "Granite 3.3"; "vLLM on Red Hat OpenShift AI" → "vLLM on OpenShift AI" */
export function modelLine(m: ModelInfo): string {
  const v = m.name.match(/^([a-z]+)-(\d+(?:\.\d+)?)/i);
  const name = v ? `${v[1][0].toUpperCase()}${v[1].slice(1)} ${v[2]}` : m.name;
  const parts = [name, m.servedBy.replace("Red Hat ", "")];
  if (m.traceId) parts.push(`trace ${m.traceId}`);
  if (m.guardrails) parts.push(`guardrails ${m.guardrails}`);
  return parts.join(" · ");
}
