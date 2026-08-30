# Demo

A three-minute walkthrough, with the exact commands.

## Before you start

```bash
make setup        # Python environment and dependencies
make demo         # harness, simulator, MCP servers, API, web UI
scripts/reset-demo.sh
```

`reset-demo.sh` puts production back in its broken state, clears any approval,
empties the timeline, and restores the agent's working clone. Run it before every
run — the demo is repeatable by design.

Open <http://localhost:3000>.

Check the opening scene is right:

```bash
curl -s localhost:8000/health | python3 -m json.tool
# status "degraded", error_rate ~0.31, deployed_revision "4c21"
```

## The script

### 0:00–0:20 — the incident

The left panel shows a SEV-1: **31% error rate, 4.2s p95, deployment 4c21**.

> "Checkout is failing for about a third of customers. Normally this is where an
> engineer starts crossing between metrics, logs, git blame and a terminal.
> Instead I'm handing the incident to PatchPilot."

Click **Investigate**.

### 0:20–1:00 — investigation

The timeline fills with real tool calls as the Detective agent works:

```
Reading the incident report
Checking production health
Querying production metrics
Reviewing deployment history
Collecting stack traces
```

Then the root cause, with evidence:

> "It correlated the error spike with deployment 4c21, found the matching
> ZeroDivisionError, and located it at checkout.py line 42. Note the evidence —
> it cites the error rate it actually measured, not a guess."

### 1:00–1:30 — reproduction in the sandbox

> "A cause you can't reproduce is a theory. So it writes a script containing the
> real faulty code and runs it in the harness sandbox."

The timeline shows `Running code in the sandbox`, then the failure. Point out that
this is real execution, isolated from the project.

### 1:30–2:00 — the fix

> "Now it branches, applies a one-line fix, adds a regression test, and runs the
> suite. Every one of those is checked independently — the agent's own report
> isn't evidence."

The evidence panel ticks over: **Fix applied → Tests pass → Regression test added
→ Pull request exists.** Open the PR on GitHub if you have a spare moment.

### 2:00–2:20 — review

Show Qodo's review on the pull request, and `docs/QODO.md` if there is time.

> "Qodo reviews this the same way it reviews my own pull requests. It caught two
> real holes in this project's safety model, which is in the README."

### 2:20–2:40 — the gate

The workflow stops. The bar across the bottom reads **PRODUCTION ACTION
REQUIRED**.

> "This is the part I care about most. The agent has stopped. Not because it chose
> to be polite — the harness suspended its loop, so the model isn't running. And
> even if it were, `deploy_production` doesn't take an approval token, it looks
> one up, and only this button creates one."

Optionally prove it — in another terminal, while paused:

```bash
curl -s localhost:8000/health | python3 -c \
  "import json,sys;h=json.load(sys.stdin);print(h['deployed_revision'], h['status'])"
# still 4c21 degraded
```

### 2:40 — approve

Click **Approve deployment**, then confirm.

### 2:40–3:00 — recovery

The deployment runs and health checks stream in. The error rate falls and the
banner turns green: **Incident resolved — production has recovered.**

> "The error rate falls because the service genuinely stopped raising — the
> deployment replaced the code it runs. PatchPilot doesn't replace the engineer.
> It replaces the hours between the alert and the decision, and keeps the decision
> human."

## Showing the rejection path

Worth 20 seconds if you have them. Reset, run again, and click **Reject**:
production is untouched, the decision is recorded, and the session and all its
evidence remain.

## If something goes wrong mid-demo

The workflow fails loudly rather than pretending. If a stage cannot be verified,
the timeline says which and why, and production is left alone. That is the system
behaving correctly, and it is worth saying so rather than restarting.

The local model is the slowest part. If a stage is taking a while, `docs/README`
notes the timings to expect; the harness log is at `.trueforge/server.log` and the
API log at `.run/api.log`.
