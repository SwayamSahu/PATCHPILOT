#!/usr/bin/env python3
"""Run one PatchPilot agent through the TrueForge harness and print what it did.

A command-line view of the same session the UI shows: which tools the agent
called, in what order, and what it concluded. Useful for developing an agent
without the frontend, and for confirming from a terminal that the harness really
is doing the work.

    scripts/run_agent.py patchpilot-detective "Investigate INC-4021"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_servers.common.config import get  # noqa: E402

API = get("TRUEFORGE_API_BASE", "http://localhost:8790/api/v1").rstrip("/")

DEFAULT_PROMPT = (
    "Incident INC-4021: checkout failures have exceeded the production threshold. "
    "Investigate and identify the root cause."
)


def unwrap(payload: dict) -> dict:
    """Responses are wrapped in `data`; unwrap when present."""
    return payload.get("data", payload)


def checked(response: httpx.Response, what: str) -> dict:
    """Return the JSON body, or fail with a diagnostic rather than a traceback.

    Indexing the body without checking the status turns an ordinary mistake - an
    unknown agent name, an invalid manifest - into a KeyError several frames from
    the cause, which is a much worse thing to hand someone at a terminal.
    """
    if response.status_code >= 400:
        raise SystemExit(f"error: {what} failed ({response.status_code}): {response.text[:300]}")
    return unwrap(response.json())


def session_events(session_id: str, limit: int = 200) -> list:
    """Fetch a session's events, oldest first, following pagination.

    The endpoint pages backwards from the newest event and caps at 100 per page,
    so a single request silently drops the beginning of an investigation — which
    is exactly the part worth reading.
    """
    collected, cursor = [], None
    while True:
        params = {"limit": min(100, limit)}
        if cursor:
            params["page_token"] = cursor
        response = httpx.get(f"{API}/sessions/{session_id}/events", params=params, timeout=60)
        payload = checked(response, "listing session events")
        payload = payload if isinstance(payload, dict) else {"data": payload}
        collected.extend(payload.get("data", []))
        cursor = (payload.get("pagination") or {}).get("next_page_token")
        if not cursor or len(collected) >= limit:
            break
    # Events arrive newest-first; the caller wants the story in order.
    return list(reversed(collected))


def run(agent: str, prompt: str, timeout_s: int = 900) -> int:
    created = httpx.post(f"{API}/sessions", json={"agent": {"name": agent}}, timeout=60)
    session = checked(created, f"creating a session for agent {agent!r}")
    session_id = session["id"]
    print(f"agent   : {agent}")
    print(f"session : {session_id}")

    turn = checked(
        httpx.post(
            f"{API}/sessions/{session_id}/turns",
            json={"input": [{"type": "user.message", "content": prompt}], "stream": False},
            timeout=120,
        ),
        "starting a turn",
    )
    turn_id = turn["id"]
    print(f"turn    : {turn_id}\n")

    deadline = time.time() + timeout_s
    state = "running"
    while time.time() < deadline:
        time.sleep(5)
        try:
            current = unwrap(
                httpx.get(f"{API}/sessions/{session_id}/turns/{turn_id}", timeout=60).json()
            )
        except httpx.HTTPError:
            # The harness is busy driving the model; a slow poll is not a failure.
            continue
        state = (current.get("state") or {}).get("status", "running")
        if state != "running":
            break
    else:
        print("timed out waiting for the turn to finish")
        return 1

    final = ""
    print("--- what the agent did ---")
    for item in session_events(session_id):
        event = item["event"]
        for call in event.get("tool_calls") or []:
            function = call.get("function", call)
            print(f"  tool  {function.get('name')}  {str(function.get('arguments'))[:70]}")
        if event["type"] == "tool.approval_required":
            names = [c.get("name") for c in event.get("tool_calls", [])]
            print(f"  PAUSED for human approval: {', '.join(filter(None, names))}")
        if event["type"] == "model.message":
            content = event.get("content")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            if content and content.strip():
                final = content

    print(f"\n--- result ({state}) ---")
    print(final or "(no textual output)")
    return 0 if state == "done" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent", nargs="?", default="patchpilot-detective")
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    return run(args.agent, args.prompt, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
