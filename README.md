# PatchPilot

**From production incident to verified fix — with a human in control.**

PatchPilot is an AI production engineer. It investigates a production incident,
identifies the root cause with evidence, reproduces the failure inside an isolated
sandbox, writes the fix and a regression test, runs them, opens a GitHub pull
request — and then stops and asks a person before it touches production.

It is built on [TrueForge](https://trueforge.dev), and the harness is doing the
work: reaching real tools over MCP, executing generated code in a sandbox,
delegating to specialised agents, and suspending its own loop at the one action
that cannot be undone.

---

## Problem

When production breaks, an engineer manually crosses metrics, logs, deployment
history and git blame; reproduces the failure; writes a fix; writes a test; opens
a pull request; deploys; and watches to see whether it actually recovered. The
work is slow and fragmented, and almost none of it is the interesting part.

The interesting part — *should this change go to production?* — takes a minute,
and it is the only part that genuinely needs a human.

## Solution

One controlled agentic loop does the fragmented work. The human keeps the decision.

```
incident → investigate → root cause → reproduce in sandbox → fix + regression test
        → pull request → code review → ⛔ HUMAN APPROVAL → deploy → verify recovery
```

## Why PatchPilot

Most agent demos narrate. This one is built so that narration doesn't count:

- **An agent's report is never evidence.** Every stage is re-verified against git,
  a test run the orchestrator performs itself, and the GitHub API. This is not
  theoretical — during development an agent reported pull request `#42` at a
  repository that does not exist, having never committed or pushed. The pipeline
  catches that class of claim by construction.
- **Production is protected by two independent locks**, neither of which the model
  participates in. A fully compromised agent still cannot deploy.
- **Recovery is real.** The error rate falls because the deployed code genuinely
  stopped raising, not because a graph was animated.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ apps/web — Next.js + TypeScript + Tailwind                   │
│ incident │ agent timeline │ evidence │ APPROVAL GATE         │
└───────────────────────────┬──────────────────────────────────┘
                            │ SSE
┌───────────────────────────┴──────────────────────────────────┐
│ apps/api — FastAPI orchestrator                              │
│  • drives the pipeline    • verifies every stage             │
│  • mints approval tokens  • resumes the paused turn          │
└───────────────────────────┬──────────────────────────────────┘
                            │ /api/v1
┌───────────────────────────┴──────────────────────────────────┐
│ TrueForge  (agent loop · sandbox · approval gate · sessions)  │
└───┬──────────────┬───────────────┬───────────────────────────┘
    │ MCP          │ MCP           │ MCP
┌───┴─────┐  ┌─────┴──────┐  ┌─────┴───────┐
│telemetry│  │ repository │  │ deployment  │
└───┬─────┘  └─────┬──────┘  └─────┬───────┘
    │              │               │
┌───┴──────────────┴───────────────┴────────┐
│ checkout-api simulator  ·  real git + GitHub│
└─────────────────────────────────────────────┘
```

Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); the verified TrueForge
API surface this is built against is in
[docs/TRUEFORGE-NOTES.md](docs/TRUEFORGE-NOTES.md).

## TrueForge usage

TrueForge is not decorative here. Four things PatchPilot depends on are harness
primitives, and removing TrueForge removes them:

| Need | TrueForge primitive |
|---|---|
| Stop before an irreversible action | `require_approval_for_tools` suspends the loop; resume with a `user.tool_approval` turn item |
| An agent that *cannot* reach a dangerous tool | `enable_tools: ["@read-only"]` removes it from the schema list entirely |
| Real code execution in isolation | provider-backed sandbox, `config.sandbox.enabled` |
| An investigation that survives a refresh | server-side sessions and event replay |
| Specialist delegation | separate agents, observable as threads |

## MCP tools

Three MCP servers, 23 tools, all reaching real systems over HTTP.

**Telemetry** (read-only): `get_service_health`, `get_metrics`, `query_logs`,
`get_recent_deployments`, `get_incident_details`, `get_error_samples`

**Repository**: `get_repository_file`, `get_git_history`, `get_commit`,
`get_diff`, `get_working_diff`, `get_pull_request`, `run_git_tests`,
`create_branch`, `edit_file`, `append_to_file`, `write_file`, `commit_changes`,
`push_branch`, `create_pull_request`

**Deployment**: `get_staging_health`, `get_production_health`,
`prepare_production_deployment`, `deploy_staging`, `deploy_production`

Every tool carries MCP annotations. That is what makes `@read-only` scoping
*mean* something — see the note in Design decisions.

## Agent architecture

| Agent | Job | Tools it can see | Can it write? |
|---|---|---|---|
| **Detective** | metrics, logs, deployments, git → root cause with evidence | telemetry, repository `@read-only` | no |
| **Reproducer** | minimal repro, executed in the sandbox | repository `@read-only`, sandbox | sandbox only |
| **Developer** | minimal fix, regression test, branch, PR | repository `@all` | branch only |
| **Validator** | reads the diff for scope creep and coverage | repository `@read-only` | no |
| **Orchestrator** | takes the change to the production gate | 3 deployment tools | production **gated** |

Scoping is configuration, not instruction: the Detective doesn't merely decline to
modify code — the tools are absent from its schema list.

## Sandbox safety

Generated code runs in the TrueForge sandbox, never in the API process. No
production credentials, execution timeouts, and captured stdout/stderr/exit code.

> **Which sandbox is running:** with a valid Daytona key, execution happens in a
> remote Daytona VM. Without one, TrueForge falls back to its **local sandbox** —
> still real, still isolated by the harness, but on your machine. `make configure`
> tells you which is in play. It is never claimed to be the stronger one.

## Human approval

The most important feature, and the one worth testing yourself.

1. The orchestrator calls `deploy_production`.
2. **The harness suspends the loop** and emits `tool.approval_required`. The model
   is not running.
3. The UI shows what will happen, what was verified, and that it is irreversible.
4. `POST /api/approve` — the only code path that mints an approval — records it.
5. The turn resumes with a `user.tool_approval` item and the deployment executes.

`deploy_production` **does not accept a token; it looks one up.** A model that
invented one has nowhere to put it, and a test asserts the signature stays that
way. Approvals are single-use, expiring, and bound to a SHA-256 digest of the
exact artifact, read from git at an immutable commit.

Verified: while paused at the gate, production stayed on `4c21` and kept returning
HTTP 500. Only the approval endpoint moved it.

## Qodo Code Review Evidence

Qodo was installed before any implementation code existed and has reviewed every
pull request since. Two review rounds across five pull requests produced **22
findings**, and two of them were genuine holes in this project's safety model.

| PR | What it covers | Review |
|---|---|---|
| [#5](https://github.com/SwayamSahu/PATCHPILOT/pull/5) | scaffold, verified TrueForge notes | 2 findings, both fixed |
| [#6](https://github.com/SwayamSahu/PATCHPILOT/pull/6) | deterministic simulator | 7 findings, all fixed |
| [#7](https://github.com/SwayamSahu/PATCHPILOT/pull/7) | telemetry MCP server | 2 findings, both fixed |
| [#8](https://github.com/SwayamSahu/PATCHPILOT/pull/8) | **the production approval gate** | 3 findings, all fixed |
| [#9](https://github.com/SwayamSahu/PATCHPILOT/pull/9) | repository MCP server | reviewed |

**What Qodo caught that mattered most** — both on [PR #8](https://github.com/SwayamSahu/PATCHPILOT/pull/8):

- **Staging could reach production.** `deploy_staging` posted to the same endpoint
  that replaces production's live module, so the one unguarded write could ship
  code to production — walking around the approval gate entirely. Staging is now a
  genuinely separate environment.
- **An approval authorised an id, not a change.** The approval was consumed by
  `deployment_id` and then arbitrary source was deployed, so a human could approve
  a reviewed one-line fix while different code shipped. Deployments are now
  content-addressed and the digest is checked *before* the approval is spent.

**What was dismissed:** Qodo recommended keeping the hybrid TypeScript/Python
split. We kept it at the time and later moved the orchestrator to Python anyway —
not to standardise, but because the approval token store is Python and putting an
HTTP hop in the middle of the safety-critical path bought nothing. Reasoning in
[docs/QODO.md](docs/QODO.md).

Full finding-by-finding record: **[docs/QODO.md](docs/QODO.md)**.

## Demo

Three-minute script with exact commands: **[docs/DEMO.md](docs/DEMO.md)**.

## Screenshots

The interface is three panels and one control:

- **Left — the incident.** Severity, live error rate and p95 latency, the
  deployment ledger with the faulty revision marked, and the agent's permission
  boundaries with production shown as `BLOCKED`.
- **Centre — the agent timeline.** Every tool call as it happens, the root cause
  with its evidence, the sandbox reproduction, test results, and the pull request.
- **Right — the evidence.** Root cause, confidence, and a verification checklist
  that reflects checks re-run against git, the test suite and the GitHub API — a
  check that has not run reads as pending, never as passing.
- **Bottom — the approval gate.** Appears only while the harness is genuinely
  paused, states that the action is irreversible, and requires a confirm step.

Images are captured from a live run rather than mocked up; see
[docs/DEMO.md](docs/DEMO.md) for the moments worth capturing.

## Installation

Requires **Python 3.11+**, **Node 22.14+**, [uv](https://docs.astral.sh/uv/), and
[Ollama](https://ollama.com).

```bash
git clone https://github.com/SwayamSahu/PATCHPILOT
cd PATCHPILOT
cp .env.example .env      # then fill in GITHUB_TOKEN
make setup
```

Pull the model and serve it with a large enough context window:

```bash
ollama pull qwen3:8b
OLLAMA_CONTEXT_LENGTH=32768 ollama serve
```

The context length matters: the agent loop carries tool schemas plus accumulated
results, and a small window truncates silently, after which tool calls start
failing in confusing ways.

## Environment variables

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | branches, commits, pull requests (Contents + Pull requests: read/write) |
| `GITHUB_OWNER`, `GITHUB_REPO` | the repository the agent works in |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY` | the OpenAI-compatible endpoint (Ollama; any non-empty key) |
| `PATCHPILOT_ORCHESTRATOR_MODEL`, `PATCHPILOT_SUBAGENT_MODEL` | model ids |
| `DAYTONA_API_KEY` | optional; remote sandbox instead of the local fallback |
| `PATCHPILOT_BASE_BRANCH` | the branch the agent's clone is based on |

`.env` is gitignored. No credentials are committed.

## Running locally

```bash
make demo     # harness, simulator, MCP servers, API, and web UI
```

Then open <http://localhost:3000>. Individual pieces:

```bash
make harness     # TrueForge on :8790
make services    # simulator :8000, MCP servers :8101-:8103
make configure   # model provider, sandbox, connectors, agents
make api         # orchestrator API on :8080
make stop
```

## Running the demo

```bash
scripts/reset-demo.sh   # production broken again, timeline cleared
```

Open the UI and click **Investigate**. See [docs/DEMO.md](docs/DEMO.md).

## Testing

```bash
make test
```

Covers the simulator and its determinism, every MCP server against a live service
in a separate process, the path jail, tool annotations, verification, the API, and
the production boundary. The most important is
`test_production_deploy_without_approval_is_refused`, which asserts against the
**live service** rather than a return value: after an unapproved attempt,
production must genuinely still be serving the faulty code.

## Security

Threat model, boundaries, and what is deliberately refused:
**[docs/SECURITY.md](docs/SECURITY.md)**.

## Project structure

```
apps/
  api/patchpilot/    orchestrator: workflow, verification, events, state, API
  web/               Next.js UI
agents/              agent manifests — the permission model, in git
mcp_servers/
  telemetry/  repository/  deployment/
simulator/checkout-service/    the production service under investigation
scripts/             setup, run, and demo-reset scripts
tests/               110+ tests
docs/                architecture, security, demo, Qodo, TrueForge notes
```

## Design decisions

**Tool annotations are the permission model.** TrueForge resolves `@read-only` and
`@write` from MCP annotations. Ours had none, so `enable_tools: ["@read-only"]`
was granting **zero** tools — an agent scoped that way received nothing and
returned an empty message. The claim that scoping was enforced by configuration
was true in intent and inert in practice until every tool was annotated.

**The artifact never passes through the model.** The deployment tools read the
file from git at an immutable commit rather than accepting `source`. There is
nothing to substitute, and the arguments stay small enough for a small model to
get right. Security and reliability pointed the same way.

**Production runs a working copy.** `checkout.py` is the source of truth in git;
the service executes a copy under `.state/live/`. Deploying *is* the act of moving
source into that copy — which is why repo and production can legitimately
disagree until a deployment happens, the situation the whole product is about.

**Development is three narrow turns**, not one instruction: apply the fix, add the
test, publish. Each is verified before the next begins, so a failure is localised
rather than surfacing as a vague dead end.

## Limitations

- **Production is simulated.** A hackathon has no production to break. The
  simulation is a real HTTP service with real state that the MCP tools query over
  the network, and deploying genuinely changes its behaviour — but it is a
  simulation, and is never presented otherwise. Git, GitHub, the pull requests and
  the reviews are all real.
- **The model is the weak link.** On a free local 8B model, investigation and
  reproduction are reliable and the one-line fix is correct, but writing a
  *working* regression test is not consistently successful. The pipeline fails
  honestly when that happens rather than proceeding. A larger model improves this;
  none of the safety properties depend on model quality.
- **It is slow.** A local 8B model with a 32k context takes minutes per stage.
- **One incident.** The workflow is built around the checkout defect, not as a
  general incident platform.
- No authentication; safe to run locally, not hardened for shared use.

## Future work

- Address Qodo review findings on the agent's own pull request automatically, then
  request a re-review — the loop is already there, it just isn't wired end to end.
- Multiple concurrent incidents with per-incident sessions.
- Richer root-cause search when the correlation is not a single deployment.

## License

MIT — see [LICENSE](LICENSE).
