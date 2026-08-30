"""Independent verification of what an agent claims to have done.

An agent's closing summary is a *claim*, not a fact. During development the
Developer agent reported this:

    {"tests_passed": true, "pr_number": 42,
     "pr_url": "https://github.com/patchpilot/simulator/pull/42"}

It had never called `create_pull_request`, never committed, and never pushed. The
pull request did not exist. The fix it wrote was genuinely correct, which is
precisely what makes the fabrication dangerous: a pipeline that trusted the
summary would have proceeded to the approval gate showing a human a PR link that
went nowhere, backed by tests that had never passed.

So nothing here reads the model's prose. Every fact is re-established from
sources the agent does not author:

* the git working tree and branch state,
* the exit code of a test run performed by this module,
* the GitHub API's answer about whether a pull request exists.

A stage that cannot be verified fails. It does not "probably" pass.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from mcp_servers.repository import github_api, gitrepo  # noqa: E402
from mcp_servers.repository.github_api import GitHubError  # noqa: E402

__all__ = ["GitHubError", "Verdict", "github_api", "gitrepo"]
from mcp_servers.repository.gitrepo import RepositoryError  # noqa: E402


@dataclass
class Verdict:
    """The outcome of verifying one stage."""

    ok: bool
    summary: str
    facts: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)

    @classmethod
    def failed(cls, summary: str, problems: list, facts: dict | None = None) -> Verdict:
        return cls(ok=False, summary=summary, facts=facts or {}, problems=problems)


def verify_reproduction(events: list) -> Verdict:
    """A reproduction counts only if code actually ran in the sandbox.

    Checked against the event stream rather than the agent's summary: the agent
    must have invoked an execution tool, because a failure it merely described is
    not a failure it observed.
    """
    executed = [e for e in events if e.get("tool") in {"exec", "run_code", "shell"}]
    if not executed:
        return Verdict.failed(
            "no code was executed in the sandbox",
            ["the reproduction was described but never run"],
        )
    return Verdict(
        ok=True,
        summary=f"reproduction executed in the sandbox ({len(executed)} runs)",
        facts={"sandbox_runs": len(executed)},
    )


def verify_patch(expected_files: list | None = None) -> Verdict:
    """Confirm a change really exists in the working tree."""
    problems, facts = [], {}
    try:
        branch = gitrepo.current_branch()
        diff = gitrepo.working_diff()["diff"]
        facts["branch"] = branch
    except RepositoryError as exc:
        return Verdict.failed("could not inspect the repository", [str(exc)])

    if branch in {"main", "master"}:
        problems.append(f"work is on {branch!r}; a fix must live on its own branch")
    if not diff.strip():
        problems.append("no changes are present in the working tree")

    changed = [
        line.split(" b/")[-1]
        for line in diff.splitlines()
        if line.startswith("diff --git")
    ]
    facts["files_changed"] = changed
    for expected in expected_files or []:
        if not any(expected in path for path in changed):
            problems.append(f"expected a change to {expected!r}, but none was made")

    if problems:
        return Verdict.failed("the patch could not be verified", problems, facts)
    return Verdict(
        ok=True,
        summary=f"{len(changed)} file(s) changed on {branch}",
        facts=facts,
    )


def verify_tests(target: str = "tests/test_simulator.py") -> Verdict:
    """Run the tests here rather than believing a report about them."""
    result = gitrepo.run_tests(target)
    if result.get("timed_out"):
        return Verdict.failed("the test run timed out", [result.get("summary", "")])
    if not result.get("passed"):
        tail = (result.get("stdout") or "").strip().splitlines()[-12:]
        return Verdict.failed(
            "tests did not pass",
            [result.get("summary") or "non-zero exit", *tail],
            {"exit_code": result.get("exit_code")},
        )
    return Verdict(
        ok=True,
        summary=result.get("summary", "tests passed"),
        facts={"exit_code": 0, "summary": result.get("summary")},
    )


def verify_regression_test(diff_text: str) -> Verdict:
    """A fix without a test that would have caught it is not finished."""
    added = [
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    added_tests = [line for line in added if line.strip().startswith("def test_")]
    if not added_tests:
        return Verdict.failed(
            "no regression test was added",
            ["the diff adds no new test function"],
        )
    return Verdict(
        ok=True,
        summary=f"{len(added_tests)} regression test(s) added",
        facts={"tests_added": [t.strip() for t in added_tests]},
    )


def verify_pull_request(pr_number: int | None) -> Verdict:
    """Ask GitHub whether the pull request exists, rather than taking a number.

    A number in a model's summary is a string it produced. This is the check that
    catches an invented PR, and it must run before a human is shown a link.
    """
    if not pr_number:
        return Verdict.failed("no pull request number was produced", ["nothing to verify"])
    try:
        pr = github_api.get_pull_request(int(pr_number))
    except GitHubError as exc:
        return Verdict.failed(
            f"pull request #{pr_number} could not be found on GitHub",
            [str(exc), "the reported pull request does not exist"],
        )
    return Verdict(
        ok=True,
        summary=f"pull request #{pr['number']} exists ({pr['state']})",
        facts={
            "number": pr["number"],
            "url": pr["url"],
            "state": pr["state"],
            "head": pr["head"],
            "changed_files": pr.get("changed_files"),
        },
    )


def verify_deployment_readiness(verdicts: dict) -> Verdict:
    """Everything must hold before a human is asked to approve a deployment.

    The human approval gate is the last line of defence, not the only one. Asking
    someone to approve a change whose tests never ran wastes the one judgement
    that matters.
    """
    failed = [name for name, verdict in verdicts.items() if not verdict.ok]
    if failed:
        return Verdict.failed(
            "not ready for human review",
            [f"{name}: {verdicts[name].summary}" for name in failed],
        )
    return Verdict(
        ok=True,
        summary="all checks passed; ready for human review",
        facts={name: verdict.facts for name, verdict in verdicts.items()},
    )
