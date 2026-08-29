# TrueForge — Verified API Notes (Phase 0)

Everything here was read from the live TrueForge docs and the pinned OpenAPI spec
(`docs/trueforge-openapi-0.2.0-rc.0.json`, `TrueForge API 0.2.0-rc.0`, OpenAPI 3.1.0)
on 2026-08-30. **Nothing in this file is assumed.** PatchPilot is built against these
facts, not against the build spec's guesses.

## Runtime

| Fact | Value |
|---|---|
| Distribution | npm — `npx @truefoundry/trueforge@latest` |
| Node requirement | >= 22.14 (local: v24.5.0 ✓) |
| Local mode | single process, SQLite, `http://localhost:8790` |
| Hosted mode | Docker Compose, Postgres + Redis, `http://localhost:8791` |
| SDK | `@truefoundry/trueforge-sdk` |
| UI SDK | `@truefoundry/trueforge-ui` |
| API base | `/api/v1` |

### Consequence for PatchPilot
The harness is Node/TypeScript. The build spec asked for a Python/FastAPI backend;
we use a **TypeScript orchestrator** (`apps/api`) so it can use the SDK directly,
and keep **Python for the MCP servers and the simulator**, where FastAPI is the
right tool. This is a deliberate, documented deviation — see ARCHITECTURE.md.

## Core object model

```
Agent  (saved definition: model + instructions + mcp_servers + config)
  └── Session   POST /api/v1/sessions            { agent }
        └── Turn    POST /api/v1/sessions/{id}/turns   { input[], previous_turn_id, stream }
              └── Events (SSE while running; queryable after)
```

Turns chain via `previous_turn_id`. Sessions persist server-side — **this is
Phase 14 (persistent session) for free**; a browser refresh replays events rather
than restarting an investigation.

### Endpoints we depend on
```
POST   /api/v1/sessions                                   create session
GET    /api/v1/sessions/{id}                              read session
GET    /api/v1/sessions/{id}/events                       full event history (replay)
POST   /api/v1/sessions/{id}/turns                        start OR resume a turn
GET    /api/v1/sessions/{id}/turns/{tid}/events           turn event history
GET    /api/v1/sessions/{id}/turns/{tid}/subscribe        SSE live stream
POST   /api/v1/sessions/{id}/cancel                       cancel running turn
POST   /api/v1/agents                                     create agent
POST/PUT /api/v1/settings/mcp-servers                     register connectors
POST/PUT /api/v1/settings/model-providers                 configure Anthropic
GET/PUT  /api/v1/settings/sandbox-providers               configure Daytona
GET    /api/v1/mcp-servers/{name}/tools                   verify tools are live
```

## Event stream

Emitted as one JSON object at a time. Types we map into PatchPilot's event model:

```
turn.created            turn.done
model.message           model.message.delta
mcp.initialize          tool.response
tool.approval_required  mcp.auth_required
thread.created          thread.done      <- subagent lifecycle
sandbox.created
```

`ToolApprovalRequiredEvent` carries `{ type, id, created_at, thread_id, tool_calls[] }`.

## The approval gate — first-class, not simulated

**Pause.** Declared per-agent, per-MCP-server:

```jsonc
{ "name": "patchpilot-deployment",
  "enable_tools": ["@all"],
  "require_approval_for_tools": ["deploy_production"] }
```

`require_approval_for_tools` accepts `@all`, `@write`, `@destructive`, or literal
tool names (default `["@write","@destructive"]`). When the agent calls a matching
tool the harness **stops the loop** and emits `tool.approval_required`.

**Resume.** A new turn carrying an approval item — never a user message:

```jsonc
POST /api/v1/sessions/{id}/turns
{ "previous_turn_id": "<paused turn>",
  "input": [{ "type": "user.tool_approval",
              "thread_id": "<from event>",
              "tool_call_id": "<from event>",
              "approval": { "status": "allow" } }] }
```

Deny is `{ "status": "deny", "reason": "..." }`, surfaced to the agent.

> The spec warns "do not mix user messages with approval or tool-response items."

### Two independent locks on production
1. **Harness lock** — `require_approval_for_tools` physically suspends the agent
   loop. The model cannot proceed by reasoning; the loop is not running.
2. **Server lock** — `deploy_production` asks the PatchPilot API out-of-band whether
   a human approval exists for that deployment, and burns a single-use token.
   **The token never enters model context**, so it cannot be guessed, forged, or
   replayed from the transcript.

Either lock alone would satisfy the brief. Both together mean a compromised prompt
still cannot ship to production. This is the project's central safety claim.

## Per-agent tool scoping (the §27 permission model, enforced by config)

`MCPServer.enable_tools` accepts `@all`, `@read-only`, or literal names;
`disable_tools` subtracts. So Detective genuinely *cannot see* a write tool —
it is absent from its schema list, not merely discouraged in a prompt.

| Agent | Connectors | enable_tools |
|---|---|---|
| detective | telemetry, repository | `@read-only` |
| reproducer | repository, + sandbox | `@read-only` |
| developer | repository | `@all` |
| validator | repository | `@read-only` |
| orchestrator | deployment | `@all`, approval on `deploy_production` |

## Sandbox

Provider-backed, configured at `PUT /api/v1/settings/sandbox-providers`; Daytona
auth is `{ "api_key": "..." }`. Enabled per agent via `config.sandbox.enabled: true`
(default `false` — must be set explicitly). `file_downloads` defaults true and
files come back via `GET .../turns/{tid}/download-sandbox-file`.

## Subagents

`config.dynamic_sub_agents.enabled` (default `true`) lets the orchestrator spawn
subagents; each runs as its own **thread**, observable via `thread.created` /
`thread.done`. PatchPilot maps these to `SUBAGENT_DELEGATED` events.

## Other runtime config worth knowing
`iteration_limit` (default 100, max 1024), context compaction and
large-tool-response offloading (both on by default), `ask_user_questions`,
`generative_ui`.

## Open items to confirm during Block E
- Whether named/static subagents can be declared, vs dynamic-only
  (`https://trueforge.dev/key-features/subagents.md`).
- Exact SSE framing on `/subscribe` (event names vs bare data lines).
- Whether `response_format` can pin the Detective's JSON output schema.
