"use client";

import type { Health } from "@/lib/types";

interface Props {
  incident: any;
  health: Health | null;
  deployments: any[];
}

/**
 * What happened. The first question a responder asks, answered without scrolling.
 */
export function IncidentPanel({ incident, health, deployments }: Props) {
  const resolved = health?.status === "healthy";
  const faulty = deployments.find((d) => !d.healthy);

  return (
    <aside className="flex h-full flex-col gap-4 overflow-y-auto border-r border-edge bg-panel p-5 scroll-quiet">
      <div>
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${resolved ? "bg-ok" : "bg-bad animate-pulse"}`} />
          <span className={`text-xs tracking-widest ${resolved ? "text-ok" : "text-bad"}`}>
            {resolved ? "RESOLVED" : "SEV-1"}
          </span>
        </div>
        <h1 className="mt-2 text-lg font-semibold">Checkout API</h1>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          {incident?.title ?? "Checkout failures above the production threshold"}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Metric
          label="Error rate"
          value={health ? `${(health.error_rate * 100).toFixed(1)}%` : "—"}
          bad={!resolved}
        />
        <Metric
          label="p95 latency"
          value={health ? `${(health.p95_latency_ms / 1000).toFixed(1)}s` : "—"}
          bad={!resolved}
        />
      </div>

      <div>
        <SectionLabel>Deployments</SectionLabel>
        <ul className="mt-2 space-y-1">
          {deployments.slice(-5).map((d) => (
            <li
              key={d.revision_id + d.deployed_at}
              className="flex items-center justify-between rounded border border-edge px-2 py-1.5 text-xs"
            >
              <span className="flex items-center gap-2">
                <span className={`h-1.5 w-1.5 rounded-full ${d.healthy ? "bg-ok" : "bg-bad"}`} />
                <span className={d.healthy ? "text-muted" : "text-bad"}>{d.revision_id}</span>
              </span>
              <span className="truncate pl-2 text-right text-[10px] text-muted">{d.summary}</span>
            </li>
          ))}
        </ul>
        {faulty && (
          <p className="mt-2 text-[11px] leading-relaxed text-muted">
            Errors began immediately after{" "}
            <span className="text-bad">{faulty.revision_id}</span>.
          </p>
        )}
      </div>

      <div className="mt-auto">
        <SectionLabel>Agent permissions</SectionLabel>
        <ul className="mt-2 space-y-1 text-[11px]">
          <Permission label="Metrics, logs, repository" value="READ" tone="ok" />
          <Permission label="Sandbox execution" value="ISOLATED" tone="ok" />
          <Permission label="Branch, commit, pull request" value="ALLOWED" tone="ok" />
          <Permission label="Production deployment" value="BLOCKED" tone="bad" />
        </ul>
        <p className="mt-2 text-[10px] leading-relaxed text-muted">
          Only a human can unlock production. The agent has no tool that grants it.
        </p>
      </div>
    </aside>
  );
}

function Metric({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className="rounded border border-edge p-3">
      <div className="text-[10px] uppercase tracking-widest text-muted">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${bad ? "text-bad" : "text-ok"}`}>{value}</div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="text-[10px] uppercase tracking-widest text-muted">{children}</div>;
}

function Permission({ label, value, tone }: { label: string; value: string; tone: "ok" | "bad" }) {
  return (
    <li className="flex items-center justify-between">
      <span className="text-muted">{label}</span>
      <span className={tone === "ok" ? "text-ok" : "text-bad"}>{value}</span>
    </li>
  );
}
