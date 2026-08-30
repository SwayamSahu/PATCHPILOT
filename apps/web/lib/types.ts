export type WorkflowState =
  | "IDLE" | "INVESTIGATING" | "ROOT_CAUSE_FOUND" | "REPRODUCING"
  | "FAILURE_REPRODUCED" | "DEVELOPING" | "TESTING" | "PR_CREATED"
  | "QODO_REVIEW" | "AWAITING_APPROVAL" | "DEPLOYING" | "VERIFYING"
  | "RESOLVED" | "REJECTED" | "FAILED";

export interface AgentEvent {
  id: string;
  timestamp: string;
  type: string;
  summary: string;
  agent?: string | null;
  tool?: string | null;
  status: "started" | "completed" | "failed";
  detail: Record<string, unknown>;
}

export interface Verdict {
  ok: boolean;
  summary: string;
  facts?: Record<string, unknown>;
  problems?: string[];
}

export interface Snapshot {
  state: WorkflowState;
  events: AgentEvent[];
  findings: Record<string, any>;
  verdicts: Record<string, Verdict>;
  approval: Record<string, any>;
  error?: string;
}

export interface Health {
  status: string;
  error_rate: number;
  p95_latency_ms: number;
  deployed_revision: string;
}
