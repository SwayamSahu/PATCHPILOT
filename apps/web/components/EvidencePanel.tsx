"use client";

import type { Snapshot } from "@/lib/types";

/**
 * What the agent knows, and how it knows it.
 *
 * Each row is a verification result produced by the backend re-checking the
 * claim, not by the agent asserting it. A check that has not run reads as
 * pending rather than as passing.
 */
export function EvidencePanel({ snapshot }: { snapshot: Snapshot }) {
  const rootCause = snapshot.findings?.root_cause;
  const repro = snapshot.findings?.reproduction;
  const pr = snapshot.findings?.pull_request;
  const recovery = snapshot.findings?.recovery as any[] | undefined;

  return (
    <aside className="flex h-full flex-col gap-5 overflow-y-auto border-l border-edge bg-panel p-5 scroll-quiet">
      <div>
        <Label>Root cause</Label>
        {rootCause ? (
          <>
            <p className="mt-2 text-xs leading-relaxed">{rootCause.root_cause}</p>
            <dl className="mt-3 space-y-1 text-[11px]">
              <Fact k="Deployment" v={rootCause.suspected_deployment} tone="bad" />
              <Fact
                k="Location"
                v={rootCause.file ? `${rootCause.file}:${rootCause.line ?? "?"}` : "—"}
              />
              <Fact
                k="Confidence"
                v={
                  typeof rootCause.confidence === "number"
                    ? `${Math.round(rootCause.confidence * 100)}%`
                    : "—"
                }
                tone="ok"
              />
            </dl>
          </>
        ) : (
          <Pending>Not yet identified.</Pending>
        )}
      </div>

      {repro && (
        <div>
          <Label>Sandbox reproduction</Label>
          <p className="mt-2 text-xs">
            <span className="text-bad">{repro.error ?? "failure"}</span>
            <span className="text-muted"> — {repro.message ?? "reproduced in isolation"}</span>
          </p>
        </div>
      )}

      <div>
        <Label>Verification</Label>
        <ul className="mt-2 space-y-1.5 text-[11px]">
          <Check name="Failure reproduced" verdict={snapshot.verdicts?.reproduction} />
          <Check name="Fix applied" verdict={snapshot.verdicts?.patch} />
          <Check name="Tests pass" verdict={snapshot.verdicts?.tests} />
          <Check name="Regression test added" verdict={snapshot.verdicts?.regression_test} />
          <Check name="Pull request exists" verdict={snapshot.verdicts?.pull_request} />
        </ul>
        <p className="mt-2 text-[10px] leading-relaxed text-muted">
          Each check is re-run against git, the test suite and the GitHub API. The agent&apos;s own
          report is not evidence.
        </p>
      </div>

      {pr?.url && (
        <div>
          <Label>Pull request</Label>
          <a
            href={pr.url}
            target="_blank"
            rel="noreferrer"
            className="mt-2 block text-xs text-accent underline underline-offset-2"
          >
            #{pr.number} — {pr.head}
          </a>
          <p className="mt-1 text-[10px] text-muted">
            {pr.changed_files ?? "?"} file(s) changed
          </p>
        </div>
      )}

      {recovery && recovery.length > 0 && (
        <div>
          <Label>Production recovery</Label>
          <div className="mt-2 space-y-1">
            {recovery.map((sample, i) => (
              <div key={i} className="flex items-center justify-between text-[11px]">
                <span className="text-muted">{(sample.error_rate * 100).toFixed(1)}%</span>
                <div className="mx-2 h-1 flex-1 rounded bg-edge">
                  <div
                    className={`h-1 rounded ${sample.status === "healthy" ? "bg-ok" : "bg-bad"}`}
                    style={{ width: `${Math.min(100, sample.error_rate * 300)}%` }}
                  />
                </div>
                <span className={sample.status === "healthy" ? "text-ok" : "text-bad"}>
                  {sample.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <div className="text-[10px] uppercase tracking-widest text-muted">{children}</div>;
}

function Pending({ children }: { children: React.ReactNode }) {
  return <p className="mt-2 text-xs text-muted">{children}</p>;
}

function Fact({ k, v, tone }: { k: string; v: any; tone?: "ok" | "bad" }) {
  return (
    <div className="flex justify-between">
      <dt className="text-muted">{k}</dt>
      <dd className={tone === "bad" ? "text-bad" : tone === "ok" ? "text-ok" : ""}>{v ?? "—"}</dd>
    </div>
  );
}

function Check({ name, verdict }: { name: string; verdict?: { ok: boolean; summary: string } }) {
  const mark = verdict ? (verdict.ok ? "✓" : "✕") : "·";
  const tone = verdict ? (verdict.ok ? "text-ok" : "text-bad") : "text-muted";
  return (
    <li className="flex items-start justify-between gap-2">
      <span className="text-muted">{name}</span>
      <span className={`shrink-0 ${tone}`} title={verdict?.summary}>
        {mark}
      </span>
    </li>
  );
}
