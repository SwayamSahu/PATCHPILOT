"""Tests for the orchestrator API.

The endpoint that matters is /api/approve: it is the only path by which a
production approval comes into existence. These tests hold that boundary in
place — approving when nothing is waiting must not silently create one, and
rejecting must leave production untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from patchpilot import state, workflow  # noqa: E402
from patchpilot.events import WorkflowState  # noqa: E402
from patchpilot.main import app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "workflow.json")
    state.reset()
    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_a_fresh_workflow_is_idle(client):
    body = client.get("/api/workflow").json()
    assert body["state"] == "IDLE"
    assert body["events"] == []


def test_approving_when_nothing_is_pending_is_refused(client):
    """An approval must correspond to a deployment a human was actually shown."""
    response = client.post("/api/approve", json={"actor": "engineer"})
    assert response.status_code == 409
    assert "awaiting approval" in response.json()["detail"]


def test_rejecting_when_nothing_is_pending_is_refused(client):
    assert client.post("/api/reject", json={"actor": "engineer"}).status_code == 409


def test_investigation_cannot_be_started_twice(client, monkeypatch):
    monkeypatch.setattr(workflow, "run_until_approval", lambda: None)
    assert client.post("/api/investigate").status_code == 200
    state.current().state = WorkflowState.INVESTIGATING
    assert client.post("/api/investigate").status_code == 409


def test_workflow_survives_a_restart(client, tmp_path, monkeypatch):
    """A refresh replays the timeline instead of restarting the investigation."""
    from patchpilot.events import Event, EventType

    workflow_state = state.current()
    workflow_state.record(Event(EventType.ROOT_CAUSE_FOUND, "Root cause: division by zero"))
    workflow_state.state = WorkflowState.ROOT_CAUSE_FOUND
    state.save()

    # Simulate a fresh process picking the state back up off disk.
    monkeypatch.setattr(state, "_current", None)
    restored = client.get("/api/workflow").json()
    assert restored["state"] == "ROOT_CAUSE_FOUND"
    assert restored["events"][0]["summary"].startswith("Root cause")


def test_the_event_stream_replays_history_before_streaming(client):
    """A client joining late must receive the whole story, not just the tail."""
    from patchpilot.events import Event, EventType
    from patchpilot.main import frames_since

    state.current().record(Event(EventType.INCIDENT_RECEIVED, "Incident INC-4021 received"))
    state.current().record(Event(EventType.ROOT_CAUSE_FOUND, "Root cause: division by zero"))
    state.save()

    frames, cursor = frames_since(0)
    assert cursor == 2
    assert "INCIDENT_RECEIVED" in frames[0]
    assert "ROOT_CAUSE_FOUND" in frames[1]
    assert '"type": "STATE"' in frames[-1], "a state frame closes every batch"


def test_the_event_stream_does_not_resend_events(client):
    from patchpilot.events import Event, EventType
    from patchpilot.main import frames_since

    state.current().record(Event(EventType.INCIDENT_RECEIVED, "Incident received"))
    _, cursor = frames_since(0)
    frames, _ = frames_since(cursor)
    assert len(frames) == 1, "only the state frame should remain"
    assert '"type": "STATE"' in frames[0]
