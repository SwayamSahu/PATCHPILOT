# Security

PatchPilot is an agent that writes code and can reach production. That combination
deserves a threat model rather than a paragraph of reassurance.

The guiding assumption is unflattering and deliberate: **assume the agent is
wrong, or has been made to act against you.** A model can be mistaken, and it can
be steered by text it reads — an incident description, a log line, a source
comment are all attacker-controllable in a real deployment. Every boundary below
holds even if the model is fully compromised, because none of them depend on the
model's cooperation.

## What an attacker would want

| Goal | Route they would take |
|---|---|
| Ship malicious code to production | Persuade the agent to deploy something |
| Exfiltrate credentials | Get a secret into a tool result, a commit, or a log |
| Escape the repository | Write outside the project through a tool |
| Gain code execution in CI | Modify a workflow file, which runs on push |
| Fake success | Report work that never happened |

Each is addressed below.

## 1. Production deployment

**Two independent locks, neither of which the model participates in.**

**The harness lock.** `deploy_production` is listed in the orchestrator's
`require_approval_for_tools`, so TrueForge suspends the agent loop when it is
called. The model is not executing while it waits. There is no prompt that
proceeds from a stopped loop, because nothing is running to read it.

**The server lock.** The deployment MCP server refuses unless a human approval
exists for that specific deployment:

* `deploy_production` **does not accept a token — it looks one up.** A model that
  invented a token has nowhere to put it, and a test asserts the signature stays
  that way.
* Approvals are **minted only by the application**, in `POST /api/approve`, in
  response to a person. No tool, agent, or prompt reaches that code path.
* Approvals are **single use**, so a replayed call after a rollback cannot
  redeploy.
* Approvals **expire** (15 minutes), so one left open overnight is not still live.
* Approvals are **bound to the exact artifact** by a SHA-256 digest taken when the
  deployment was prepared. Approving change X and shipping change Y is refused.

**The artifact never passes through the model.** `prepare` and `deploy` read the
file from the repository at an immutable commit. There is no `source` parameter to
substitute, and the digest is re-checked against the same commit at deploy time.

> Verified behaviour: with a deployment paused at the gate, production remained on
> the faulty revision `4c21` and continued returning HTTP 500. Only the approval
> endpoint moved it.

## 2. Filesystem access

Every repository path is jailed:

* absolute paths are rejected;
* `..` traversal is rejected;
* the check runs against the **fully resolved** path, so a symlink pointing out of
  the repository is caught rather than followed.

Some paths are refused even inside the repository:

| Path | Why |
|---|---|
| `.git/` | history rewriting, and hooks that execute on commit |
| `.github/workflows/` | an agent that can edit CI has arbitrary code execution on push |
| `.env` | secrets |

None are needed to fix an application bug, so permitting them would buy nothing.

The agent also works in a dedicated clone under `.workrepo/`, never a developer's
checkout.

## 3. Secrets

* Credentials live in `.env`, which is gitignored. `.env.example` carries no values.
* Git credentials are supplied through a `GIT_ASKPASS` helper, so the token never
  reaches the remote URL, `.git/config`, or a command line that might be logged.
* Command output is redacted before it is returned, and **a test asserts no tool
  result contains a token** — tool results travel directly into model context.
* The sandbox is provisioned without production credentials.

## 4. Code execution

Generated code runs in the harness sandbox, never in the API process. The sandbox
is isolated from the project, has an execution timeout, and captures stdout,
stderr, and exit code.

**Running the test suite is also code execution**, and it is worth being explicit
about it. `run_git_tests` invokes pytest, which imports and runs whatever the
agent wrote — including code that never passes through `write_file` and therefore
never meets the path jail. Three things contain that:

* **No credentials in the environment.** The subprocess receives only `PATH`,
  `HOME`, `TMPDIR`, locale, and `PYTHONPATH`. Every credential-shaped variable is
  withheld, because redacting output afterwards is not a defence — code can
  encode a secret, write it to a file, or send it over the network. A test that
  reads `GITHUB_TOKEN` gets `None`, and a test asserts exactly that.
* **No option injection.** A target beginning with `-` is rejected and the target
  is passed after `--`, so `--basetemp=/somewhere` cannot become an option that
  pytest acts on — it clears the directory it names.
* **A throwaway working directory.** Tests run in `.workrepo/`, a disposable
  clone, never a developer's checkout.

The residual risk is honest: a hostile test can still touch files the *host user*
can write. What it cannot do is read a credential or reach the real repository.
For a hackathon project run locally that is the right balance; a production
deployment would run tests inside the sandbox too.

## 4a. Getting a change into the repository

Two routes were open and are now closed:

* **`git add -A` staged everything.** A file dropped into the working tree by a
  test — a CI workflow, say — would be committed, smuggling it past the write
  rules on the way to a pull request. Commits now stage explicitly and refuse
  outright if an off-limits path has changed.
* **`push` took a refspec.** The branch argument went to git verbatim, so
  `HEAD:main` would push the working tree straight onto the base branch and skip
  the pull request entirely, and `--all` would push everything. Only a plain
  branch name is accepted, expanded to an explicit refspec.

## 5. Fabricated results

An agent's summary is a claim, not a fact — and this is not hypothetical. During
development the Developer agent reported a pull request (`#42`, at a repository
that does not exist) having never committed, pushed, or called
`create_pull_request`.

Nothing in the pipeline advances on an agent's word. Every stage is re-established
from sources the agent does not author: the git working tree, a test run the
orchestrator performs itself, and the GitHub API. A stage that cannot be verified
fails the workflow.

This matters most at the approval gate: asking a person to approve a deployment on
the strength of tests that never ran would waste the one judgement in the system
that matters.

## 6. Scope of change

The Developer agent uses `edit_file`, which replaces one **unique** quoted string
and refuses an ambiguous match, so an over-broad rewrite is hard to perform by
accident. The Validator agent independently reads the diff for unrelated changes.

## What this is not

This is a hackathon project with a simulated production environment. It has no
authentication, no multi-tenancy, and no audit retention beyond a local file. It
is safe to run locally; it is not hardened for shared or internet-facing use, and
TrueForge says the same about its own standalone mode.

## Reporting

Open an issue on the repository. Do not include credentials in the report.
