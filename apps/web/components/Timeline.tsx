"use client";

import { useEffect, useRef } from "react";
import type { AgentEvent } from "@/lib/types";

/**
 * What the agent is doing, and what it has already done.
 *
 * Every row corresponds to an event the backend recorded from the harness or
 * from a verification step. Nothing here is animated to imply progress that did
 * not happen.
 */
export function Timeline({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length]);

  return (
    <section className="flex h-full flex-col overflow-hidden bg-ink">
      <header className="flex items-center justify-between border-b border-edge px-5 py-3">
        <h2 className="text-xs uppercase tracking-widest text-muted">Agent activity</h2>
        {running && (
          <span className="flex items-center gap-2 text-[10px] text-accent">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
            working
          </span>
        )}
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-4 scroll-quiet">
        {events.length === 0 ? (
          <p className="text-xs text-muted">
            No activity yet. Start the investigation to hand this incident to PatchPilot.
          </p>
        ) : (
          <ol className="space-y-1.5">
            {events.map((event) => (
              <Row key={event.id} event={event} />
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </div>
    </section>
  );
}

const AGENT_LABEL: Record<string, string> = {
  "patchpilot-detective": "detective",
  "patchpilot-reproducer": "reproducer",
  "patchpilot-developer": "developer",
  "patchpilot-validator": "validator",
  "patchpilot-orchestrator": "orchestrator",
};

function Row({ event }: { event: AgentEvent }) {
  const { mark, tone } = presentation(event);
  const time = new Date(event.timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  return (
    <li className="flex items-start gap-3 text-xs leading-relaxed">
      <span className="w-16 shrink-0 pt-0.5 text-[10px] text-muted">{time}</span>
      <span className={`w-4 shrink-0 pt-0.5 ${tone}`}>{mark}</span>
      <span className="flex-1">
        <span className={tone}>{event.summary}</span>
        {event.agent && (
          <span className="ml-2 text-[10px] text-muted">{AGENT_LABEL[event.agent] ?? ""}</span>
        )}
        {event.type === "ROOT_CAUSE_FOUND" && <RootCause detail={event.detail} />}
        {event.type === "ERROR" && Array.isArray((event.detail as any)?.problems) && (
          <ul className="mt-1 space-y-0.5">
            {((event.detail as any).problems as string[]).slice(0, 4).map((p, i) => (
              <li key={i} className="text-[10px] text-bad">
                {p}
              </li>
            ))}
          </ul>
        )}
      </span>
    </li>
  );
}

function RootCause({ detail }: { detail: Record<string, unknown> }) {
  const evidence = (detail?.evidence as string[]) ?? [];
  if (!evidence.length) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {evidence.slice(0, 4).map((line, i) => (
        <li key={i} className="text-[10px] text-muted">
          — {line}
        </li>
      ))}
    </ul>
  );
}

function presentation(event: AgentEvent): { mark: string; tone: string } {
  if (event.status === "failed") return { mark: "✕", tone: "text-bad" };
  if (event.status === "started") return { mark: "▸", tone: "text-muted" };

  switch (event.type) {
    case "APPROVAL_REQUIRED":
      return { mark: "⏸", tone: "text-accent" };
    case "ROOT_CAUSE_FOUND":
    case "INCIDENT_RESOLVED":
    case "DEPLOYMENT_COMPLETE":
      return { mark: "✓", tone: "text-ok" };
    case "VERIFICATION":
      return { mark: "✓", tone: "text-ok" };
    case "SANDBOX_RESULT":
      return { mark: "▣", tone: "text-ok" };
    case "REJECTED":
      return { mark: "⊘", tone: "text-warn" };
    case "ERROR":
      return { mark: "✕", tone: "text-bad" };
    default:
      return { mark: "✓", tone: "text-[#c9ccd4]" };
  }
}
