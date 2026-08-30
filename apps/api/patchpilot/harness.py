"""A thin client for the TrueForge harness.

Only what PatchPilot needs: start a session, run a turn, read the event stream,
and resume a turn that has paused for human approval.

The approval resume is the piece worth reading closely. When an agent calls a
gated tool, TrueForge stops the loop and emits `tool.approval_required`. Nothing
proceeds until a new turn arrives carrying a `user.tool_approval` item naming the
exact thread and tool call. The model is not running while it waits, so it cannot
argue its way forward; the only thing that moves the workflow on is a person.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mcp_servers.common.config import get  # noqa: E402

API = get("TRUEFORGE_API_BASE", "http://localhost:8790/api/v1").rstrip("/")
TIMEOUT = httpx.Timeout(120.0, connect=10.0)

EVENT_PAGE_LIMIT = 100


class HarnessError(RuntimeError):
    """The harness could not be reached, or refused a request."""


@dataclass
class PendingApproval:
    """A tool call the harness has suspended, waiting on a person."""

    thread_id: str
    tool_call_id: str
    tool_name: str


def _request(method: str, path: str, **kwargs) -> dict:
    try:
        response = httpx.request(method, f"{API}{path}", timeout=TIMEOUT, **kwargs)
    except httpx.HTTPError as exc:
        raise HarnessError(f"could not reach the harness at {API}: {exc}") from exc
    if response.status_code >= 400:
        raise HarnessError(
            f"harness returned {response.status_code} for {path}: {response.text[:300]}"
        )
    payload = response.json()
    return payload.get("data", payload)


def create_session(agent: str) -> str:
    return _request("POST", "/sessions", json={"agent": {"name": agent}})["id"]


def start_turn(session_id: str, prompt: str, previous_turn_id: str | None = None) -> str:
    body = {"input": [{"type": "user.message", "content": prompt}], "stream": False}
    if previous_turn_id:
        body["previous_turn_id"] = previous_turn_id
    return _request("POST", f"/sessions/{session_id}/turns", json=body)["id"]


def resume_with_approval(
    session_id: str, previous_turn_id: str, approval: PendingApproval, allow: bool,
    reason: str | None = None,
) -> str:
    """Resume a suspended turn with a human's decision.

    The decision is the entire payload — there is deliberately no user message
    alongside it, which the API forbids anyway. Denial carries a reason so the
    agent learns why rather than simply failing.
    """
    decision = {"status": "allow"} if allow else {
        "status": "deny",
        "reason": reason or "A human rejected this production deployment.",
    }
    body = {
        "previous_turn_id": previous_turn_id,
        "input": [
            {
                "type": "user.tool_approval",
                "thread_id": approval.thread_id,
                "tool_call_id": approval.tool_call_id,
                "approval": decision,
            }
        ],
        "stream": False,
    }
    return _request("POST", f"/sessions/{session_id}/turns", json=body)["id"]


def turn_state(session_id: str, turn_id: str) -> str:
    turn = _request("GET", f"/sessions/{session_id}/turns/{turn_id}")
    return (turn.get("state") or {}).get("status", "running")


def session_events(session_id: str, limit: int = 500) -> list:
    """All events for a session, oldest first.

    The endpoint pages backwards from the newest event and caps each page at 100,
    so a single request returns only the most recent slice. For replay after a
    refresh that would silently discard the start of the investigation — the part
    a returning user most needs — so pagination is followed to exhaustion.
    """
    collected, cursor = [], None
    while len(collected) < limit:
        params = {"limit": EVENT_PAGE_LIMIT}
        if cursor:
            params["page_token"] = cursor
        payload = _request("GET", f"/sessions/{session_id}/events", params=params)
        page = payload if isinstance(payload, list) else payload.get("data", [])
        collected.extend(page)
        pagination = {} if isinstance(payload, list) else (payload.get("pagination") or {})
        cursor = pagination.get("next_page_token")
        if not cursor or not page:
            break
    return [item.get("event", item) for item in reversed(collected)]


def find_pending_approval(session_id: str) -> PendingApproval | None:
    """The approval the harness is currently waiting on, if any."""
    for event in reversed(session_events(session_id)):
        if event.get("type") == "tool.approval_required":
            calls = event.get("tool_calls") or []
            if not calls:
                continue
            call = calls[0]
            # The pause event identifies the call but does not repeat its name, so
            # the name is recovered from the model message that issued it. The UI
            # needs to tell a person which action is waiting on them.
            return PendingApproval(
                thread_id=event.get("thread_id", "main"),
                tool_call_id=call.get("id") or call.get("tool_call_id", ""),
                tool_name=call.get("name") or _tool_name_for(session_id, call.get("id", "")),
            )
    return None


def _tool_name_for(session_id: str, tool_call_id: str) -> str:
    """Find which tool a pending call refers to, by id, in the message stream."""
    for event in reversed(session_events(session_id)):
        for call in event.get("tool_calls") or []:
            if call.get("id") == tool_call_id:
                return (call.get("function") or call).get("name", "")
    return ""


def wait_for_turn(session_id: str, turn_id: str, timeout_s: int = 1800, poll_s: float = 3.0) -> str:
    """Block until a turn finishes or pauses.

    A turn that stops for approval reports as done, so callers must check for a
    pending approval rather than assuming completion means the work is finished.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = turn_state(session_id, turn_id)
        if state != "running":
            return state
        time.sleep(poll_s)
    raise HarnessError(f"turn {turn_id} did not finish within {timeout_s}s")
