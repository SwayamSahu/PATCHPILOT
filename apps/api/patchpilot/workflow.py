"""The PatchPilot pipeline.

Runs the incident from alert to a verified fix, delegating each stage to a
specialised agent through the harness, and — this is the part that matters —
**checking the result of every stage independently before moving on**.

An agent's closing summary is treated as a proposal. What advances the workflow
is evidence: an execution that really happened in the sandbox, a diff that really
exists in git, a test run this process performed, a pull request GitHub confirms.
A stage whose claim cannot be corroborated fails the workflow rather than
quietly passing it along, because the alternative is asking a human to approve a
production deployment on the strength of a sentence a model wrote.

Development is deliberately split into three narrow turns — apply the fix, add
the test, publish — rather than one long instruction. Smaller steps are easier to
verify individually, and each one gets checked before the next begins.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mcp_servers.deployment import approvals  # noqa: E402
from mcp_servers.repository import gitrepo  # noqa: E402

from . import harness, state, verification  # noqa: E402
from .events import Event, EventType, Status, WorkflowState  # noqa: E402

DEPLOY_PATH = "simulator/checkout-service/app/checkout.py"
TEST_PATH = "tests/test_simulator.py"
BASE_BRANCH_FALLBACK = "main"
FIX_BRANCH = "fix/checkout-discount"

_run_lock = threading.Lock()


class StageFailed(RuntimeError):
    """A stage could not be verified. The workflow stops here, loudly."""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _emit(event: Event) -> None:
    workflow = state.current()
    workflow.record(event)
    state.save()


def _set_state(new_state: WorkflowState) -> None:
    workflow = state.current()
    workflow.state = new_state
    state.save()


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of an agent's reply.

    Agents are asked for bare JSON and usually comply, but a small model often
    wraps it in prose or a code fence. Recovering the object is worth doing; what
    is *not* done is trusting its contents, which is what verification is for.
    """
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else ""
    try:
        return json.loads(candidate) if candidate else {}
    except json.JSONDecodeError:
        return {}


def _run_agent(agent: str, prompt: str, timeout_s: int = 1800) -> tuple[dict, list]:
    """Run one agent turn and return (parsed reply, tool calls actually made)."""
    workflow = state.current()
    session_id = workflow.sessions.get(agent) or harness.create_session(agent)
    workflow.sessions[agent] = session_id
    state.save()

    label = agent.replace("patchpilot-", "").title()
    _emit(Event(EventType.AGENT_STARTED, f"{label} agent started",
                agent=agent, status=Status.STARTED))

    turn_id = harness.start_turn(session_id, prompt)
    harness.wait_for_turn(session_id, turn_id, timeout_s=timeout_s)

    events = harness.session_events(session_id)
    tool_calls, final_text = [], ""
    tool_names: dict = {}
    for event in events:
        for patchpilot_event in _translate(event, agent, tool_names):
            _emit(patchpilot_event)
        for call in event.get("tool_calls") or []:
            function = call.get("function", call)
            if function.get("name"):
                tool_calls.append(
                    {"tool": function["name"], "arguments": function.get("arguments")}
                )
        if event.get("type") == "model.message":
            content = event.get("content")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            if content and content.strip():
                final_text = content
    return _extract_json(final_text), tool_calls


def _translate(raw: dict, agent: str, tool_names: dict) -> list:
    from .events import from_harness_event

    # Only surface tool activity and pauses; agent lifecycle is emitted by hand so
    # the timeline reads as stages rather than as harness internals.
    translated = from_harness_event(raw, agent, tool_names)
    return [e for e in translated if e.type != EventType.AGENT_STARTED]


def _fail(stage: str, verdict: verification.Verdict) -> None:
    workflow = state.current()
    workflow.verdicts[stage] = {
        "ok": False, "summary": verdict.summary, "problems": verdict.problems,
    }
    workflow.error = f"{stage}: {verdict.summary}"
    _emit(Event(EventType.ERROR, f"{stage} could not be verified: {verdict.summary}",
                status=Status.FAILED, detail={"problems": verdict.problems}))
    _set_state(WorkflowState.FAILED)
    state.save()
    raise StageFailed(f"{stage}: {verdict.summary}")


def _pass(stage: str, verdict: verification.Verdict) -> None:
    workflow = state.current()
    workflow.verdicts[stage] = {"ok": True, "summary": verdict.summary, "facts": verdict.facts}
    _emit(Event(EventType.VERIFICATION, f"Verified: {verdict.summary}", detail=verdict.facts))
    state.save()


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------


def investigate() -> dict:
    _set_state(WorkflowState.INVESTIGATING)
    reply, _ = _run_agent(
        "patchpilot-detective",
        "Incident INC-4021: checkout failures have exceeded the production threshold. "
        "Investigate and identify the root cause.",
    )
    if not reply.get("root_cause"):
        _fail("investigation", verification.Verdict.failed(
            "no root cause was produced", ["the detective returned no usable finding"]))

    workflow = state.current()
    workflow.findings["root_cause"] = reply
    _emit(Event(EventType.ROOT_CAUSE_FOUND,
                f"Root cause: {reply.get('root_cause', '')[:160]}",
                agent="patchpilot-detective",
                detail={
                    "deployment": reply.get("suspected_deployment"),
                    "file": reply.get("file"),
                    "line": reply.get("line"),
                    "confidence": reply.get("confidence"),
                    "evidence": reply.get("evidence", []),
                }))
    _set_state(WorkflowState.ROOT_CAUSE_FOUND)
    return reply


def reproduce(root_cause: dict) -> dict:
    _set_state(WorkflowState.REPRODUCING)
    reply, tool_calls = _run_agent(
        "patchpilot-reproducer",
        f"Reproduce this defect in the sandbox. Root cause: {root_cause.get('root_cause')}. "
        f"File: {DEPLOY_PATH}. A cart with subtotal=100.0 and discount=0.0 triggers it.\n\n"
        "Read that file, write a self-contained script that pastes in Cart and compute_total, "
        "call it with subtotal=100.0 discount=0.0 inside try/except, print the exception, "
        "and run it in the sandbox.",
    )
    verdict = verification.verify_reproduction(tool_calls)
    if not verdict.ok:
        _fail("reproduction", verdict)
    _pass("reproduction", verdict)

    state.current().findings["reproduction"] = reply
    _emit(Event(EventType.SANDBOX_RESULT,
                f"Failure reproduced in the sandbox: {reply.get('error', 'see output')}",
                agent="patchpilot-reproducer", detail=reply))
    _set_state(WorkflowState.FAILURE_REPRODUCED)
    return reply


def develop(root_cause: dict) -> dict:
    """Apply the fix, add a regression test, and publish — as three narrow turns."""
    _set_state(WorkflowState.DEVELOPING)
    base = gitrepo.base_branch() or BASE_BRANCH_FALLBACK

    _run_agent(
        "patchpilot-developer",
        f"Step 1 of 3. Create branch '{FIX_BRANCH}' from {base}, then fix exactly one line in "
        f"{DEPLOY_PATH} using edit_file: replace\n"
        "    return round(cart.subtotal / cart.discount, 2)\n"
        "with\n"
        "    return round(cart.subtotal * (1 - cart.discount), 2)\n"
        "Change nothing else. Do not add tests yet, do not commit yet.",
    )
    verdict = verification.verify_patch(expected_files=[DEPLOY_PATH])
    if not verdict.ok:
        _fail("patch", verdict)
    _pass("patch", verdict)
    _emit(Event(EventType.PATCH_GENERATED, "Fix applied to checkout.py",
                agent="patchpilot-developer", detail=verdict.facts))

    _set_state(WorkflowState.TESTING)
    _emit(Event(EventType.TEST_STARTED, "Running the regression suite", status=Status.STARTED))
    _run_agent(
        "patchpilot-developer",
        f"Step 2 of 3. Append a regression test to {TEST_PATH} using append_to_file, then run "
        f"run_git_tests on {TEST_PATH} and fix your test until it passes. Add no imports; use "
        "the existing 'client' fixture. POST /checkout takes "
        '{"subtotal": ..., "discount": ...} '
        'and returns {"status": "ok", "order": {..., "total": ...}}. Cover a zero-discount cart '
        "(total 100.0, no error) and a 25% discount on 100.0 (total 75.0).",
    )

    tests = verification.verify_tests(TEST_PATH)
    regression = verification.verify_regression_test(gitrepo.working_diff()["diff"])
    if not tests.ok:
        _fail("tests", tests)
    _pass("tests", tests)
    if not regression.ok:
        _fail("regression_test", regression)
    _pass("regression_test", regression)
    _emit(Event(EventType.TEST_RESULT, tests.summary, agent="patchpilot-developer",
                detail={**tests.facts, **regression.facts}))

    reply, _ = _run_agent(
        "patchpilot-developer",
        f"Step 3 of 3. Commit the change with a conventional commit message, push branch "
        f"'{FIX_BRANCH}', and open a pull request into '{base}' describing what broke, why, and "
        "how the test proves it. Report the pull request number you received from the tool.",
    )

    pr_number = reply.get("pr_number") or _discover_pr_number()
    pr = verification.verify_pull_request(pr_number)
    if not pr.ok:
        _fail("pull_request", pr)
    _pass("pull_request", pr)

    workflow = state.current()
    workflow.findings["pull_request"] = pr.facts
    workflow.findings["commit_sha"] = gitrepo.head_sha()
    _emit(Event(EventType.PR_CREATED, f"Pull request #{pr.facts['number']} opened",
                agent="patchpilot-developer", detail=pr.facts))
    _set_state(WorkflowState.PR_CREATED)
    return pr.facts


def _discover_pr_number():
    """Find the pull request for the fix branch, without asking the agent.

    The agent's reported number is checked against GitHub anyway, but looking the
    branch up directly means an agent that pushed successfully and then garbled
    its summary does not fail a step it actually completed.
    """
    from mcp_servers.repository import github_api

    try:
        listing = github_api._request(
            "GET", f"/repos/{github_api.repo_slug()}/pulls",
            params={"state": "open", "head": f"{github_api.get('GITHUB_OWNER')}:{FIX_BRANCH}"},
        )
    except Exception:
        return None
    return listing[0]["number"] if listing else None


def validate() -> dict:
    reply, _ = _run_agent(
        "patchpilot-validator",
        "Assess whether the proposed fix is safe to put in front of a human for deployment "
        f"approval. Read the change with get_working_diff and run run_git_tests on {TEST_PATH}.",
    )
    state.current().findings["validation"] = reply
    _emit(Event(EventType.VERIFICATION, "Validator assessed the change",
                agent="patchpilot-validator", detail=reply))
    return reply


def request_deployment(pr: dict) -> dict:
    """Drive the orchestrator to the approval gate and stop there.

    The turn will suspend inside the harness. Nothing here polls it forward: the
    only thing that resumes it is a person.
    """
    _set_state(WorkflowState.QODO_REVIEW)
    workflow = state.current()
    commit_sha = workflow.findings.get("commit_sha", "")

    readiness = verification.verify_deployment_readiness({
        name: verification.Verdict(v["ok"], v["summary"], v.get("facts", {}), v.get("problems", []))
        for name, v in workflow.verdicts.items()
    })
    if not readiness.ok:
        _fail("readiness", readiness)
    _pass("readiness", readiness)

    session_id = harness.create_session("patchpilot-orchestrator")
    workflow.sessions["patchpilot-orchestrator"] = session_id
    state.save()

    turn_id = harness.start_turn(
        session_id,
        f"The fix for incident INC-4021 is ready. Deploy it to production. Details: summary "
        f"'restore multiplicative discount calculation', commit_sha {commit_sha}, "
        f"pr_number {pr.get('number', 0)}.",
    )
    harness.wait_for_turn(session_id, turn_id, timeout_s=900)

    pending = harness.find_pending_approval(session_id)
    if pending is None:
        _fail("approval_gate", verification.Verdict.failed(
            "the deployment did not stop for approval",
            ["expected the harness to suspend at deploy_production"]))

    workflow.approval = {
        "thread_id": pending.thread_id,
        "tool_call_id": pending.tool_call_id,
        "tool": pending.tool_name or "deploy_production",
        "turn_id": turn_id,
        "deployment_id": f"deploy-{pr.get('number', 0)}-{commit_sha[:7]}",
        "pr": pr,
        "status": "pending",
    }
    _emit(Event(EventType.APPROVAL_REQUIRED,
                "Production deployment requires human approval",
                agent="patchpilot-orchestrator", detail=workflow.approval))
    _set_state(WorkflowState.AWAITING_APPROVAL)
    return workflow.approval


# --------------------------------------------------------------------------
# The human decision
# --------------------------------------------------------------------------


def approve(actor: str) -> dict:
    """Mint an approval and let the suspended deployment proceed.

    This function is the only path to a production deployment, and nothing the
    model can call reaches it.
    """
    workflow = state.current()
    if workflow.state is not WorkflowState.AWAITING_APPROVAL:
        raise RuntimeError(f"nothing is awaiting approval (state is {workflow.state.value})")

    pending_info = workflow.approval
    approvals.grant(pending_info["deployment_id"], approved_by=actor)
    _emit(Event(EventType.APPROVED, f"Deployment approved by {actor}",
                detail={"deployment_id": pending_info["deployment_id"], "approved_by": actor}))
    workflow.approval["status"] = "approved"
    _set_state(WorkflowState.DEPLOYING)
    _emit(Event(EventType.DEPLOYMENT_STARTED, "Deploying to production", status=Status.STARTED))

    session_id = workflow.sessions["patchpilot-orchestrator"]
    pending = harness.PendingApproval(
        thread_id=pending_info["thread_id"],
        tool_call_id=pending_info["tool_call_id"],
        tool_name=pending_info["tool"],
    )
    turn_id = harness.resume_with_approval(session_id, pending_info["turn_id"], pending, allow=True)
    harness.wait_for_turn(session_id, turn_id, timeout_s=900)

    return _verify_recovery()


def reject(actor: str, reason: str = "") -> dict:
    """Cancel the deployment, keeping the session and its evidence intact."""
    workflow = state.current()
    if workflow.state is not WorkflowState.AWAITING_APPROVAL:
        raise RuntimeError(f"nothing is awaiting approval (state is {workflow.state.value})")

    pending_info = workflow.approval
    session_id = workflow.sessions["patchpilot-orchestrator"]
    pending = harness.PendingApproval(
        thread_id=pending_info["thread_id"],
        tool_call_id=pending_info["tool_call_id"],
        tool_name=pending_info["tool"],
    )
    harness.resume_with_approval(
        session_id, pending_info["turn_id"], pending, allow=False,
        reason=reason or f"{actor} rejected this production deployment.",
    )
    workflow.approval["status"] = "rejected"
    workflow.approval["rejected_by"] = actor
    _emit(Event(EventType.REJECTED, f"Deployment rejected by {actor}",
                detail={"reason": reason, "production_changed": False}))
    _set_state(WorkflowState.REJECTED)
    return {"status": "rejected", "production_changed": False}


def _verify_recovery() -> dict:
    """Watch production until it recovers, or report honestly that it did not."""
    from mcp_servers.common import simulator

    _set_state(WorkflowState.VERIFYING)
    samples = []
    for _ in range(6):
        health = simulator.get("/health")
        samples.append({
            "error_rate": health["error_rate"],
            "p95_latency_ms": health["p95_latency_ms"],
            "status": health["status"],
            "revision": health["deployed_revision"],
        })
        _emit(Event(EventType.HEALTH_CHECK,
                    f"Error rate {health['error_rate'] * 100:.1f}%, "
                    f"p95 {health['p95_latency_ms'] / 1000:.1f}s",
                    detail=samples[-1]))
        if health["status"] == "healthy":
            break
        import time

        time.sleep(5)

    workflow = state.current()
    workflow.findings["recovery"] = samples
    recovered = samples[-1]["status"] == "healthy"
    if recovered:
        _emit(Event(EventType.DEPLOYMENT_COMPLETE, "Production deployment complete",
                    detail=samples[-1]))
        _emit(Event(EventType.INCIDENT_RESOLVED, "Incident resolved: production has recovered",
                    detail={"samples": samples}))
        _set_state(WorkflowState.RESOLVED)
    else:
        _emit(Event(EventType.ERROR, "Production has not recovered after deployment",
                    status=Status.FAILED, detail={"samples": samples}))
        _set_state(WorkflowState.FAILED)
    return {"recovered": recovered, "samples": samples}


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------


def run_until_approval() -> None:
    """Run every autonomous stage, then stop at the human gate."""
    if not _run_lock.acquire(blocking=False):
        raise RuntimeError("an investigation is already running")
    try:
        workflow = state.current()
        _emit(Event(EventType.INCIDENT_RECEIVED,
                    f"Incident {workflow.incident_id} received: checkout failures above threshold",
                    detail={"incident_id": workflow.incident_id}))
        root_cause = investigate()
        reproduce(root_cause)
        pr = develop(root_cause)
        validate()
        request_deployment(pr)
    except StageFailed:
        pass  # already recorded as a failure event and a FAILED state
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI rather than swallowed
        _emit(Event(EventType.ERROR, f"The workflow stopped: {exc}", status=Status.FAILED))
        _set_state(WorkflowState.FAILED)
    finally:
        _run_lock.release()
