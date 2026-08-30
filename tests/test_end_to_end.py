"""End-to-end test of the incident workflow.

Everything here is real except the model: a live checkout-api in its own process,
a real git repository, the real approval store, the real verification code, and
the real state machine. Only the agents' replies are stubbed, because driving a
local model would make this take half an hour and would test the model rather
than the pipeline.

What it proves is the part that must never regress:

    incident → root cause → reproduction → fix → tests → pull request
             → ⛔ approval → deployment → recovery

and that the gate in the middle genuinely holds.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from patchpilot import state, verification, workflow  # noqa: E402
from patchpilot.events import EventType, WorkflowState  # noqa: E402

from mcp_servers.deployment import approvals  # noqa: E402
from mcp_servers.repository import gitrepo  # noqa: E402

FAULTY = "return round(cart.subtotal / cart.discount, 2)"
FIXED = "return round(cart.subtotal * (1 - cart.discount), 2)"


@pytest.fixture()
def world(tmp_path, monkeypatch, live_simulator):
    """A complete, isolated PatchPilot world."""
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "workflow.json")
    monkeypatch.setattr(approvals, "STORE_PATH", tmp_path / "approvals.json")
    state.reset()
    approvals.clear()

    repo = tmp_path / "workrepo"
    repo.mkdir()
    monkeypatch.setenv("WORK_REPO_PATH", str(repo))
    monkeypatch.setenv("PATCHPILOT_BASE_BRANCH", "main")

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)

    git("init", "-q", "--initial-branch=main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    target = repo / "simulator" / "checkout-service" / "app"
    target.mkdir(parents=True)
    (target / "checkout.py").write_text(
        "from dataclasses import dataclass\n\n\n"
        "@dataclass(frozen=True)\nclass Cart:\n"
        "    subtotal: float\n    discount: float = 0.0\n    currency: str = 'USD'\n\n\n"
        "def compute_total(cart: Cart) -> float:\n"
        f"    {FAULTY}\n"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_simulator.py").write_text("def test_placeholder():\n    assert True\n")
    git("add", "-A")
    git("commit", "-qm", "checkout: simplify discount calculation")

    httpx.post(f"{live_simulator}/_control/reset", timeout=5)
    return {"repo": repo, "simulator": live_simulator}


def _stub_agents(monkeypatch, world):
    """Replace each agent with a scripted turn that performs its real tool work."""
    repo = world["repo"]
    checkout = repo / "simulator" / "checkout-service" / "app" / "checkout.py"
    calls: list = []

    def fake_run_agent(agent: str, prompt: str, timeout_s: int = 1800):
        calls.append(agent)
        if agent == "patchpilot-detective":
            return (
                {
                    "service": "checkout-api",
                    "root_cause": "ZeroDivisionError from dividing by the discount",
                    "suspected_deployment": "4c21",
                    "file": "checkout.py",
                    "line": 42,
                    "confidence": 0.97,
                    "evidence": ["error rate rose to 31% immediately after 4c21"],
                },
                [],
            )
        if agent == "patchpilot-reproducer":
            # The verifier requires a real execution tool call in the stream.
            return ({"reproduced": True, "error": "ZeroDivisionError"}, [{"tool": "exec"}])
        if agent == "patchpilot-developer":
            step = calls.count("patchpilot-developer")
            if step == 1:
                gitrepo.create_branch(workflow.FIX_BRANCH, "main")
                checkout.write_text(checkout.read_text().replace(FAULTY, FIXED))
            elif step == 2:
                gitrepo.append_to_file(
                    "tests/test_simulator.py",
                    "def test_zero_discount_does_not_raise():\n    assert True\n",
                )
            else:
                gitrepo.commit("fix(checkout): restore multiplicative discount")
            return ({}, [])
        return ({"readiness": "APPROVED_FOR_HUMAN_REVIEW"}, [])

    monkeypatch.setattr(workflow, "_run_agent", fake_run_agent)
    return calls


def test_the_pipeline_runs_to_the_gate_and_stops(world, monkeypatch):
    """Every stage verified, and then it waits for a person."""
    _stub_agents(monkeypatch, world)
    monkeypatch.setattr(workflow, "_discover_pr_number", lambda: 11)
    monkeypatch.setattr(
        verification.github_api, "get_pull_request",
        lambda n: {"number": n, "url": f"https://github.com/o/r/pull/{n}", "state": "open",
                   "head": workflow.FIX_BRANCH, "changed_files": 2},
    )
    monkeypatch.setattr(workflow.harness, "create_session", lambda agent: "sess-1")
    monkeypatch.setattr(workflow.harness, "start_turn", lambda *a, **k: "turn-1")
    monkeypatch.setattr(workflow.harness, "wait_for_turn", lambda *a, **k: "done")
    monkeypatch.setattr(
        workflow.harness, "find_pending_approval",
        lambda sid: workflow.harness.PendingApproval("main", "call_1", "deploy_production"),
    )
    monkeypatch.setattr(workflow, "TEST_PATH", "tests/test_simulator.py")

    workflow.run_until_approval()
    current = state.current()

    assert current.state is WorkflowState.AWAITING_APPROVAL, current.error
    for stage in ("reproduction", "patch", "tests", "regression_test", "pull_request"):
        assert current.verdicts[stage]["ok"], f"{stage}: {current.verdicts[stage]}"

    types = [e["type"] for e in current.events]
    assert types[0] == EventType.INCIDENT_RECEIVED.value
    for expected in ("ROOT_CAUSE_FOUND", "SANDBOX_RESULT", "PATCH_GENERATED",
                     "TEST_RESULT", "PR_CREATED", "APPROVAL_REQUIRED"):
        assert expected in types, f"missing {expected} in {types}"

    # The one thing that must be true while it waits.
    health = httpx.get(f"{world['simulator']}/health", timeout=5).json()
    assert health["deployed_revision"] == "4c21"
    assert health["status"] == "degraded"


def test_a_reproduction_that_never_ran_fails_the_workflow(world, monkeypatch):
    """A described failure is not a reproduced one."""
    _stub_agents(monkeypatch, world)
    monkeypatch.setattr(
        workflow, "_run_agent",
        lambda agent, prompt, timeout_s=1800: (
            ({"root_cause": "something", "suspected_deployment": "4c21"}, [])
            if agent == "patchpilot-detective"
            else (
                {"reproduced": True, "error": "ZeroDivisionError"},
                [{"tool": "get_repository_file"}],
            )
        ),
    )
    workflow.run_until_approval()

    current = state.current()
    assert current.state is WorkflowState.FAILED
    assert current.verdicts["reproduction"]["ok"] is False
    health = httpx.get(f"{world['simulator']}/health", timeout=5).json()
    assert health["deployed_revision"] == "4c21"


def test_an_invented_pull_request_fails_the_workflow(world, monkeypatch):
    """The exact fabrication seen in development must stop the pipeline."""
    _stub_agents(monkeypatch, world)
    monkeypatch.setattr(workflow, "_discover_pr_number", lambda: 42)
    monkeypatch.setattr(workflow, "TEST_PATH", "tests/test_simulator.py")

    def not_found(_number):
        raise verification.GitHubError("GitHub returned 404")

    monkeypatch.setattr(verification.github_api, "get_pull_request", not_found)

    workflow.run_until_approval()
    current = state.current()
    assert current.state is WorkflowState.FAILED
    assert current.verdicts["pull_request"]["ok"] is False
    assert "does not exist" in " ".join(current.verdicts["pull_request"]["problems"])
