"use client";

import { useState } from "react";
import type { Snapshot } from "@/lib/types";

/**
 * The one irreversible action in the product, and the one decision a person keeps.
 *
 * It states plainly what will happen, what has been verified, and that it cannot
 * be undone — because an approval that is not understood is not really an
 * approval.
 */
export function ApprovalGate({
  snapshot,
  onApprove,
  onReject,
  busy,
}: {
  snapshot: Snapshot;
  onApprove: () => void;
  onReject: () => void;
  busy: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const pr = snapshot.approval?.pr;
  const checks = Object.entries(snapshot.verdicts ?? {});

  return (
    <div className="border-t border-accent/40 bg-[#17140c] px-6 py-5">
      <div className="mx-auto flex max-w-5xl flex-col gap-4">
        <div className="flex items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-2 text-accent">
              <span className="text-sm">⚠</span>
              <span className="text-xs uppercase tracking-widest">Production action required</span>
            </div>
            <p className="mt-2 text-sm">
              Deploy{" "}
              <span className="text-accent">
                {pr?.number ? `pull request #${pr.number}` : "the verified fix"}
              </span>{" "}
              to production.
            </p>
            <p className="mt-1 text-[11px] text-muted">
              This is irreversible. The agent has been paused at this point and cannot proceed
              without you — it has no tool that grants approval.
            </p>
          </div>

          <ul className="hidden shrink-0 space-y-0.5 text-[11px] sm:block">
            {checks.map(([name, verdict]) => (
              <li key={name} className="flex items-center gap-2">
                <span className={verdict.ok ? "text-ok" : "text-bad"}>{verdict.ok ? "✓" : "✕"}</span>
                <span className="text-muted">{name.replace(/_/g, " ")}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex items-center gap-3">
          {confirming ? (
            <>
              <button
                onClick={onApprove}
                disabled={busy}
                className="rounded bg-accent px-5 py-2.5 text-sm font-semibold text-ink transition hover:brightness-110 disabled:opacity-50"
              >
                {busy ? "Deploying…" : "Yes — deploy to production"}
              </button>
              <button
                onClick={() => setConfirming(false)}
                disabled={busy}
                className="rounded border border-edge px-4 py-2.5 text-sm text-muted hover:text-white"
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => setConfirming(true)}
                disabled={busy}
                className="rounded bg-accent px-5 py-2.5 text-sm font-semibold text-ink transition hover:brightness-110 disabled:opacity-50"
              >
                Approve deployment
              </button>
              <button
                onClick={onReject}
                disabled={busy}
                className="rounded border border-edge px-4 py-2.5 text-sm text-muted transition hover:border-bad hover:text-bad"
              >
                Reject
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
