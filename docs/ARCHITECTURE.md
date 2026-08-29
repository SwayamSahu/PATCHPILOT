# PatchPilot — Architecture

> Grounded in the verified TrueForge API surface recorded in
> [TRUEFORGE-NOTES.md](./TRUEFORGE-NOTES.md). Where this document departs from the
> original build specification, the deviation and its reason are stated explicitly.

## 1. What PatchPilot is

An AI production engineer. It takes a production incident from alert to a verified,
reviewed code change — investigating telemetry, reproducing the failure in an
isolated sandbox, writing the fix and its regression test, opening a pull request,
absorbing code review — and then **stops**, because deploying to production is a
human's decision.

## 2. Why TrueForge is load-bearing, not decorative

Four things PatchPilot needs are TrueForge primitives, not things we built on top:

| Need | TrueForge primitive |
|---|---|
| Stop before an irreversible action | `require_approval_for_tools` suspends the agent loop; resume via a `user.tool_approval` turn item |
| An agent that genuinely cannot reach a dangerous tool | `enable_tools: ["@read-only"]` removes it from the schema list |
| Real code execution in isolation | provider-backed sandbox (Daytona), `config.sandbox.enabled` |
| Investigation that survives a refresh | server-side sessions; `GET /sessions/{id}/events` replays |
| Specialist delegation | dynamic subagents, observable as `thread.created` / `thread.done` |

Remove TrueForge and none of this survives. That is the qualifying bar for this
hackathon, and it is met by construction rather than by narration.

## 3. Component map

```
┌──────────────────────────────────────────────────────────────┐
│ apps/web — Next.js + TS + Tailwind                           │
│ incident │ agent timeline │ evidence │ APPROVAL GATE         │
└───────────────────────────┬──────────────────────────────────┘
                            │ SSE (PatchPilot event model)
┌───────────────────────────┴──────────────────────────────────┐
│ apps/api — TypeScript orchestrator (@truefoundry/trueforge-sdk)│
│  • owns the session      • maps TrueForge events → UI events │
│  • mints approval tokens • resumes paused turns              │
└───────────────────────────┬──────────────────────────────────┘
                            │ /api/v1
┌───────────────────────────┴──────────────────────────────────┐
│ TrueForge harness  (agent loop · sandbox · approval · state)  │
└───┬──────────────┬───────────────┬───────────────────────────┘
    │ MCP          │ MCP           │ MCP
┌───┴────┐   ┌─────┴──────┐   ┌────┴────────┐
│telemetry│  │ repository │   │ deployment  │   (Python, FastAPI-backed MCP)
└───┬────┘   └─────┬──────┘   └────┬────────┘
    │              │               │
┌───┴──────────────┴───────────────┴────────┐
│ simulator — checkout-service + telemetry   │  real HTTP, real state
│ real git working repo → real GitHub PRs    │
└────────────────────────────────────────────┘
```

## 4. Language split (deviation from spec §8)

The spec asked for a Python/FastAPI backend. TrueForge is Node/TypeScript with a
TypeScript SDK, so a Python orchestrator would mean hand-rolling a client for the
most safety-critical path in the product — the approval resume. We therefore run:

- **`apps/api` in TypeScript**, on the official SDK.
- **MCP servers and the simulator in Python/FastAPI**, where the spec's intent
  (a real service the tools really call) is fully preserved.

## 5. The five agents

| Agent | Job | Tools it can see | Can it write? |
|---|---|---|---|
| **Orchestrator** | drives the workflow, delegates, holds the approval gate | deployment | production **gated** |
| **Detective** | metrics, logs, deploy history, git → root cause + confidence + evidence | telemetry, repository | no |
| **Reproducer** | minimal repro code, executed in the sandbox | repository, sandbox | sandbox only |
| **Developer** | minimal patch + regression test, branch, commit, PR | repository | branch only |
| **Validator** | diff review, scope check, readiness assessment | repository | no |

Scoping is enforced by `enable_tools`, so "Detective cannot modify code" is a
property of the configuration, not a promise in a prompt.

## 6. The safety boundary

```
INVESTIGATE   metrics R · logs R · repo R
SANDBOX       execute generated code, isolated, no production credentials
DEVELOPMENT   branch · commit · PR            (never main, never production)
PRODUCTION    BLOCKED — requires a human
```

Production is protected twice over, deliberately:

1. **Harness lock.** `require_approval_for_tools: ["deploy_production"]` suspends
   the loop. The model is not running; it cannot argue its way past this.
2. **Server lock.** The deployment MCP server asks the PatchPilot API whether a
   human approval exists for this deployment, and burns a single-use token. The
   token is never placed in model context, so it cannot be fabricated or replayed.

A prompt injection that fully captures the agent still cannot deploy.

## 7. Event model

TrueForge events are mapped to a stable PatchPilot vocabulary
(`INCIDENT_RECEIVED`, `TOOL_CALL`, `SUBAGENT_DELEGATED`, `SANDBOX_RESULT`,
`ROOT_CAUSE_FOUND`, `APPROVAL_REQUIRED`, `DEPLOYMENT_COMPLETE`,
`INCIDENT_RESOLVED`, …) and streamed to the browser. The UI renders only real
events; it never animates a step that did not occur.

## 8. What is simulated, and why — stated plainly

| Component | Real | Simulated |
|---|---|---|
| Agent harness, loop, approval, sandbox | ✅ TrueForge / Daytona | — |
| MCP tools | ✅ real servers over HTTP | — |
| Git, branches, commits, PRs, review | ✅ real git + GitHub + Qodo | — |
| Production telemetry & deployment | — | deterministic local service |

We simulate production because a hackathon has no production to break. The
simulation is a **real HTTP service with real state**: the MCP tools query it over
the network, and deploying genuinely changes its behaviour — error rate falls
because the service recovered, not because the UI animated a number. Nothing
simulated is ever presented as real.
