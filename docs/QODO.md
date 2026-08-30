# Qodo code review

Qodo has reviewed this project from its first pull request. Every substantive
change has gone through a branch, a pull request, and a review, and the findings
below were fixed before the work was built on.

## Setup

The Qodo GitHub App was installed on `SwayamSahu/PATCHPILOT` before any
implementation code existed, so PR #1 — the scaffold — was reviewed like
everything after it. No configuration beyond the app install was needed; Qodo
comments on each pull request automatically.

## Workflow

```
branch → commits → pull request → Qodo review
       → fix findings, one regression test each → push → Qodo re-review → merge
```

Pull requests are **stacked**: each targets the branch below it rather than
`main`. Each diff stays small and independently reviewable, which produces
sharper findings than one large pull request would.

## What Qodo found

Two review rounds across five pull requests produced **22 findings**. The ones
that changed the design are below; each fix carries a regression test named after
the problem it prevents.

### Findings that were genuine defects in the safety model

**Staging could reach production** (High, security). `deploy_staging` posted to
the same endpoint that replaces production's live module. The one write an agent
may perform without approval — justified on the grounds that staging is
reversible — could therefore ship code straight to production, walking around the
approval gate entirely. Staging is now a separate environment with its own source
file, module, smoke test and endpoints. `test_staging_deploy_cannot_reach_production`
asserts production's source is byte-identical afterwards.

**An approval authorised an id, not a change** (High, security).
`deploy_production` consumed the approval by `deployment_id` and then deployed
whatever source it was handed, so a human could approve a reviewed one-line fix
while different code shipped under that id. Deployments are now content-addressed
at an immutable commit and the digest is verified *before* the approval is
consumed, so a mismatched attempt cannot even burn a valid approval.

**A deployment could declare itself healthy** (High). `healthy` defaulted to true
and `source` was optional, so a deployment shipping no code made telemetry report
recovery while `/checkout` kept failing — precisely the "fake the graph" failure
this project must not have. The field was removed; health is now derived from a
smoke test against the code that ends up running.

### Findings that were real bugs

| Finding | Severity | Fix |
|---|---|---|
| A validated deploy could still fail on publish | High | validate in staging, publish atomically, roll back on failure |
| Historical errors lost their exception after the fix shipped | High | cache the sample from the faulty window; attribute errors per revision |
| Metric windows were seeded from a raw `now` | High | align to minute boundaries — the determinism claim had been vacuous, and the original test only compared overlapping timestamps |
| Reset raced with deploy | High | both take the deploy lock |
| Telemetry tools ignored their `service` argument | Medium | unknown services are rejected rather than silently answered with another service's data |
| `mcp>=1.2` allowed an environment that fails at import | High | pinned `mcp>=2.0` |
| Non-finite floats reached pricing, and broke the error path itself | Medium | rejected, plus a handler that does not echo raw input |
| Logs contained future-dated events | Medium | filtered to the horizon |
| `opened_at` was rewritten by the fix deploy | Medium | reports when the fault began |
| Makefile advertised scripts that did not exist | High | targets now match reality |
| `GET /sessions/{id}/events` documented as full history | Medium | it paginates; replay now follows `next_page_token` to exhaustion |
| Hand-rolled dotenv parser | Suggestion | adopted `python-dotenv` |

### Considered and not adopted

Qodo suggested a **Python-only orchestrator** and a **TypeScript-only backend** as
alternatives to the hybrid split, and recommended keeping the hybrid. We initially
did. We later moved the orchestrator to Python anyway — not to standardise, but
because the approval token store lives in Python and the orchestrator has to mint
into the same store the deployment server consumes from. Splitting that across a
language boundary would put an HTTP hop in the middle of the safety-critical path
for no benefit.

Qodo also flagged a **verification date** in the TrueForge notes as inconsistent
with its own clock. Rather than argue about timestamps, the date was removed in
favour of pointing at the committed OpenAPI spec, which is better provenance than
a date anyway.

## Honest note on the review trail

Pull requests #1–#4 were auto-closed by GitHub when commit authorship was
rewritten across the whole history to the correct account. They remain visible
with Qodo's original findings on them, and #5–#8 supersede them with the same
content plus the fixes. The rewrite was worth doing early; it would have been far
more disruptive later.
