"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApprovalGate } from "@/components/ApprovalGate";
import { EvidencePanel } from "@/components/EvidencePanel";
import { IncidentPanel } from "@/components/IncidentPanel";
import { Timeline } from "@/components/Timeline";
import { api } from "@/lib/api";
import type { AgentEvent, Snapshot } from "@/lib/types";

const RUNNING_STATES = new Set([
  "INVESTIGATING", "REPRODUCING", "DEVELOPING", "TESTING",
  "PR_CREATED", "QODO_REVIEW", "DEPLOYING", "VERIFYING",
]);

const EMPTY: Snapshot = {
  state: "IDLE",
  events: [],
  findings: {},
  verdicts: {},
  approval: {},
};

export default function Page() {
  const [snapshot, setSnapshot] = useState<Snapshot>(EMPTY);
  const [incident, setIncident] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const seen = useRef<Set<string>>(new Set());

  // Load whatever the server already knows. A refresh rejoins an investigation
  // in progress rather than starting a new one.
  useEffect(() => {
    api.workflow().then((data: Snapshot) => {
      data.events.forEach((e) => seen.current.add(e.id));
      setSnapshot(data);
    });
  }, []);

  useEffect(() => {
    const source = new EventSource(api.eventsUrl());
    source.onmessage = (message) => {
      const payload = JSON.parse(message.data);
      if (payload.type === "STATE") {
        setSnapshot((prev) => ({
          ...prev,
          state: payload.state,
          approval: payload.approval ?? {},
          verdicts: payload.verdicts ?? {},
          findings: payload.findings ?? {},
        }));
        return;
      }
      const event = payload as AgentEvent;
      if (seen.current.has(event.id)) return;
      seen.current.add(event.id);
      setSnapshot((prev) => ({ ...prev, events: [...prev.events, event] }));
    };
    return () => source.close();
  }, []);

  // Production health drives the incident panel, so recovery is visible as it happens.
  useEffect(() => {
    const load = () => api.incident().then(setIncident).catch(() => undefined);
    load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, []);

  const start = useCallback(async () => {
    setBusy(true);
    await api.investigate();
    setBusy(false);
  }, []);

  const approve = useCallback(async () => {
    setBusy(true);
    await api.approve("engineer");
    setBusy(false);
  }, []);

  const reject = useCallback(async () => {
    setBusy(true);
    await api.reject("engineer", "Rejected from the PatchPilot UI.");
    setBusy(false);
  }, []);

  const reset = useCallback(async () => {
    setBusy(true);
    await api.reset();
    seen.current.clear();
    setSnapshot(EMPTY);
    setBusy(false);
  }, []);

  const running = RUNNING_STATES.has(snapshot.state);
  const awaiting = snapshot.state === "AWAITING_APPROVAL";

  return (
    <main className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-edge bg-panel px-5 py-3">
        <div className="flex items-baseline gap-3">
          <span className="text-sm font-semibold tracking-tight">PatchPilot</span>
          <span className="text-[10px] text-muted">
            from production incident to verified fix — with a human in control
          </span>
        </div>
        <div className="flex items-center gap-3">
          <StateBadge state={snapshot.state} />
          {snapshot.state === "IDLE" ? (
            <button
              onClick={start}
              disabled={busy}
              className="rounded bg-accent px-4 py-1.5 text-xs font-semibold text-ink hover:brightness-110 disabled:opacity-50"
            >
              Investigate
            </button>
          ) : (
            <button
              onClick={reset}
              disabled={busy || running}
              className="rounded border border-edge px-3 py-1.5 text-xs text-muted hover:text-white disabled:opacity-40"
            >
              Reset
            </button>
          )}
        </div>
      </header>

      <div className="grid flex-1 grid-cols-[300px_1fr_320px] overflow-hidden">
        <IncidentPanel
          incident={incident?.incident}
          health={incident?.health ?? null}
          deployments={incident?.deployments ?? []}
        />
        <Timeline events={snapshot.events} running={running} />
        <EvidencePanel snapshot={snapshot} />
      </div>

      {awaiting && (
        <ApprovalGate snapshot={snapshot} onApprove={approve} onReject={reject} busy={busy} />
      )}

      {snapshot.state === "RESOLVED" && (
        <div className="border-t border-ok/40 bg-[#0d1710] px-6 py-4 text-center text-sm text-ok">
          ✓ Incident resolved — production has recovered.
        </div>
      )}

      {snapshot.state === "REJECTED" && (
        <div className="border-t border-warn/40 bg-[#17140c] px-6 py-4 text-center text-sm text-warn">
          ⊘ Deployment rejected. Production was left untouched.
        </div>
      )}

      {snapshot.state === "FAILED" && (
        <div className="border-t border-bad/40 bg-[#170d0d] px-6 py-4 text-center text-sm text-bad">
          ✕ The workflow stopped. Production remains protected.
          {snapshot.error && <span className="ml-2 text-muted">{snapshot.error}</span>}
        </div>
      )}
    </main>
  );
}

const STATE_TONE: Record<string, string> = {
  RESOLVED: "text-ok border-ok/40",
  AWAITING_APPROVAL: "text-accent border-accent/40",
  FAILED: "text-bad border-bad/40",
  REJECTED: "text-warn border-warn/40",
};

function StateBadge({ state }: { state: string }) {
  const tone = STATE_TONE[state] ?? "text-muted border-edge";
  return (
    <span className={`rounded border px-2 py-1 text-[10px] tracking-widest ${tone}`}>
      {state.replace(/_/g, " ")}
    </span>
  );
}
