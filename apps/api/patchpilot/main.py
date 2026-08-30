"""The PatchPilot API.

Serves the incident, the live event stream, and — the only endpoint that matters
for safety — the human decision on a production deployment.

The approval endpoint is the boundary. An HTTP request from the browser is the
one thing in this system that can mint a deployment approval; no agent, tool, or
prompt reaches it. Everything else here is reporting.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mcp_servers.common import simulator  # noqa: E402
from mcp_servers.common.simulator import SimulatorUnavailable  # noqa: E402

from . import state, workflow  # noqa: E402
from .events import WorkflowState  # noqa: E402

app = FastAPI(
    title="PatchPilot",
    version="1.0.0",
    description="From production incident to verified fix, with a human in control.",
)

# The UI is served from a different origin in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Decision(BaseModel):
    """A human's answer at the production gate."""

    actor: str = Field("engineer", min_length=1, description="Who is deciding.")
    reason: str = Field("", description="Why, shown to the agent when rejecting.")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/incident")
def incident() -> dict:
    """The incident as production currently reports it."""
    try:
        return {
            "incident": simulator.get("/incidents/INC-4021"),
            "health": simulator.get("/health"),
            "deployments": simulator.get("/deployments")["deployments"],
        }
    except SimulatorUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/workflow")
def get_workflow() -> dict:
    """The full workflow, including every event so far.

    A browser that reconnects reads this and redraws the timeline. It does not
    restart the investigation: that would discard completed work and repeat
    actions that already happened.
    """
    return state.current().to_dict()


@app.post("/api/investigate")
def investigate() -> dict:
    """Start the investigation. Runs to the approval gate, then stops."""
    workflow_state = state.current()
    if workflow_state.state not in (WorkflowState.IDLE, WorkflowState.FAILED,
                                    WorkflowState.REJECTED, WorkflowState.RESOLVED):
        raise HTTPException(status_code=409, detail=f"already {workflow_state.state.value}")

    thread = threading.Thread(target=workflow.run_until_approval, daemon=True)
    thread.start()
    return {"status": "started"}


@app.post("/api/approve")
def approve(decision: Decision) -> dict:
    """Approve the production deployment.

    This is the only path by which a production approval comes into existence.
    The token is minted here, server-side, and never enters model context.
    """
    try:
        result = workflow.approve(decision.actor)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "approved", **result}


@app.post("/api/reject")
def reject(decision: Decision) -> dict:
    """Decline the deployment. Production is left untouched."""
    try:
        return workflow.reject(decision.actor, decision.reason)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/reset")
def reset() -> dict:
    """Reset the workflow and the simulated production environment."""
    state.reset()
    try:
        simulator.post("/_control/reset")
    except SimulatorUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "reset"}


def frames_since(sent: int) -> tuple[list, int]:
    """Build the SSE frames a client has not seen yet, plus the new cursor.

    Separated from the endpoint so it can be tested. Driving an endless stream
    from a test hangs the test rather than proving anything about it.
    """
    workflow_state = state.current()
    events_now = workflow_state.events
    frames = [f"data: {json.dumps(event)}\n\n" for event in events_now[sent:]]
    # A state frame follows each batch so the UI can render the current stage even
    # when no new event has arrived.
    frames.append(
        "data: "
        + json.dumps({
            "type": "STATE",
            "state": workflow_state.state.value,
            "approval": workflow_state.approval,
            "verdicts": workflow_state.verdicts,
            "findings": workflow_state.findings,
        })
        + "\n\n"
    )
    return frames, len(events_now)


@app.get("/api/events")
async def events() -> StreamingResponse:
    """Stream workflow events to the browser.

    Replays everything recorded so far before streaming new ones, so a client
    joining late sees the whole story rather than the tail of it.
    """

    async def stream():
        sent = 0
        while True:
            frames, sent = frames_since(sent)
            for frame in frames:
                yield frame
            await asyncio.sleep(1.0)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
