# PatchPilot

**From production incident to verified fix — with a human in control.**

PatchPilot is an AI production engineer. It investigates a production incident,
identifies the probable root cause with evidence, reproduces the failure inside an
isolated sandbox, writes the fix and a regression test, runs them, opens a GitHub
pull request, absorbs code review — and then stops and asks a human before it
touches production.

> 🚧 **Status: under construction.** This README is filled in phase by phase as the
> project is built. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design
> and [docs/TRUEFORGE-NOTES.md](docs/TRUEFORGE-NOTES.md) for the verified harness API
> this is built against.

## Problem

When production breaks, an engineer manually crosses metrics, logs, deployment
history, and git blame; reproduces the failure; writes a fix; writes a test; opens
a PR; reviews it; deploys; and watches to see whether it actually recovered. The
work is slow and fragmented, and almost none of it is the interesting part.

## Solution

One controlled agentic loop does the fragmented work. The human keeps the one
decision that matters:

```
incident → investigate → root cause → reproduce in sandbox → fix + test
        → pull request → code review → ⛔ HUMAN APPROVAL → deploy → verify recovery
```

## Why PatchPilot

Most AI agents tell you what to do. This one does the work — and it knows when it
must stop. Production deployment is blocked by two independent locks, and neither
of them is a prompt.

---

*Remaining sections — TrueForge Usage · MCP Tools · Agent Architecture · Sandbox
Safety · Human Approval · Qodo Code Review Evidence · Demo · Installation ·
Running · Testing · Security · Design Decisions · Limitations · Future Work —
are added as their phases complete.*
