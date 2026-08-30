"""PatchPilot's event vocabulary.

TrueForge emits harness-level events — turns, threads, model messages, tool
responses. The UI needs to tell an incident story: what was found, what was run,
what is waiting on a person. This module translates one into the other.

Two rules govern the translation.

**Every event describes something that happened.** Events are produced from the
harness stream and from verification results, never from an agent's narration.
If the UI shows "failure reproduced", code ran in a sandbox and raised.

**Nothing is invented to smooth the story.** A stage that failed emits a failure
event. The timeline is a record, not a script.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class EventType(StrEnum):
    INCIDENT_RECEIVED = "INCIDENT_RECEIVED"
    AGENT_STARTED = "AGENT_STARTED"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    SUBAGENT_DELEGATED = "SUBAGENT_DELEGATED"
    SANDBOX_STARTED = "SANDBOX_STARTED"
    SANDBOX_RESULT = "SANDBOX_RESULT"
    ROOT_CAUSE_FOUND = "ROOT_CAUSE_FOUND"
    PATCH_GENERATED = "PATCH_GENERATED"
    TEST_STARTED = "TEST_STARTED"
    TEST_RESULT = "TEST_RESULT"
    PR_CREATED = "PR_CREATED"
    QODO_REVIEW = "QODO_REVIEW"
    VERIFICATION = "VERIFICATION"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEPLOYMENT_STARTED = "DEPLOYMENT_STARTED"
    DEPLOYMENT_COMPLETE = "DEPLOYMENT_COMPLETE"
    HEALTH_CHECK = "HEALTH_CHECK"
    INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
    ERROR = "ERROR"


class WorkflowState(StrEnum):
    """The states the UI renders. Transitions are driven by verified outcomes."""

    IDLE = "IDLE"
    INVESTIGATING = "INVESTIGATING"
    ROOT_CAUSE_FOUND = "ROOT_CAUSE_FOUND"
    REPRODUCING = "REPRODUCING"
    FAILURE_REPRODUCED = "FAILURE_REPRODUCED"
    DEVELOPING = "DEVELOPING"
    TESTING = "TESTING"
    PR_CREATED = "PR_CREATED"
    QODO_REVIEW = "QODO_REVIEW"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    DEPLOYING = "DEPLOYING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"

    # REJECTED is an addition to the states named in the brief. A human declining
    # a deployment is a correct outcome of the system working, not a failure of
    # it, and collapsing the two would misreport the one decision the product
    # exists to preserve.


class Status(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Event:
    """One thing that happened, as the UI will show it."""

    type: EventType
    summary: str
    agent: str | None = None
    tool: str | None = None
    status: Status = Status.COMPLETED
    detail: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["type"] = self.type.value
        payload["status"] = self.status.value
        return payload

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict())}\n\n"


# Tools whose execution means code actually ran somewhere isolated.
SANDBOX_TOOLS = {"exec", "run_code", "shell"}

# How a tool call reads in the timeline. A tool the user has never heard of is
# not information; a sentence about what the agent is finding out is.
TOOL_SUMMARIES = {
    "get_incident_details": "Reading the incident report",
    "get_service_health": "Checking production health",
    "get_metrics": "Querying production metrics",
    "query_logs": "Searching application logs",
    "get_recent_deployments": "Reviewing deployment history",
    "get_error_samples": "Collecting stack traces",
    "get_repository_file": "Reading the source file",
    "get_git_history": "Inspecting git history",
    "get_commit": "Examining the suspect commit",
    "get_diff": "Reading the commit diff",
    "get_working_diff": "Reviewing the proposed change",
    "create_branch": "Creating a branch",
    "write_file": "Writing a file",
    "edit_file": "Applying the fix",
    "append_to_file": "Adding a regression test",
    "run_git_tests": "Running the test suite",
    "commit_changes": "Committing the change",
    "push_branch": "Pushing the branch",
    "create_pull_request": "Opening a pull request",
    "get_pull_request": "Reading review feedback",
    "prepare_production_deployment": "Preparing the production deployment",
    "deploy_staging": "Deploying to staging",
    "get_staging_health": "Checking staging health",
    "get_production_health": "Checking production health",
    "deploy_production": "Deploying to production",
    "exec": "Running code in the sandbox",
}


def describe_tool(name: str) -> str:
    return TOOL_SUMMARIES.get(name, f"Calling {name}")


def from_harness_event(raw: dict, agent: str) -> list:
    """Translate one TrueForge event into zero or more PatchPilot events."""
    kind = raw.get("type")
    produced = []

    if kind == "turn.created":
        produced.append(Event(EventType.AGENT_STARTED, f"{agent} started", agent=agent,
                              status=Status.STARTED))

    elif kind == "thread.created":
        produced.append(Event(EventType.SUBAGENT_DELEGATED, "Delegated to a subagent",
                              agent=agent, detail={"thread_id": raw.get("thread_id")}))

    elif kind == "model.message":
        for call in raw.get("tool_calls") or []:
            function = call.get("function", call)
            name = function.get("name", "")
            is_sandbox = name in SANDBOX_TOOLS
            produced.append(
                Event(
                    EventType.SANDBOX_STARTED if is_sandbox else EventType.TOOL_CALL,
                    describe_tool(name),
                    agent=agent,
                    tool=name,
                    status=Status.STARTED,
                    detail={"arguments": _truncate(function.get("arguments"))},
                )
            )

    elif kind == "tool.response":
        name = raw.get("name") or raw.get("tool_name") or ""
        is_sandbox = name in SANDBOX_TOOLS
        produced.append(
            Event(
                EventType.SANDBOX_RESULT if is_sandbox else EventType.TOOL_RESULT,
                describe_tool(name),
                agent=agent,
                tool=name,
                detail={"result": _truncate(raw.get("content") or raw.get("result"))},
            )
        )

    elif kind == "tool.approval_required":
        names = [c.get("name") for c in raw.get("tool_calls", []) if c.get("name")]
        produced.append(
            Event(
                EventType.APPROVAL_REQUIRED,
                "Production deployment requires human approval",
                agent=agent,
                detail={"tools": names, "thread_id": raw.get("thread_id")},
            )
        )

    return produced


def _truncate(value, limit: int = 600):
    """Keep event payloads small enough to stream comfortably."""
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "…"
