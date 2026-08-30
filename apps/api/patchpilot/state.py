"""Persistent workflow state.

The investigation lives on the server, not in the browser. A refresh re-reads
this file and replays the timeline; it does not restart an investigation, which
would throw away work and, worse, re-run actions that already happened.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .events import Event, WorkflowState

STATE_DIR = Path(__file__).resolve().parents[3] / ".run"
STATE_PATH = STATE_DIR / "workflow.json"

_lock = threading.RLock()


@dataclass
class Workflow:
    """Everything known about one incident investigation."""

    incident_id: str = "INC-4021"
    state: WorkflowState = WorkflowState.IDLE
    events: list = field(default_factory=list)
    findings: dict = field(default_factory=dict)
    verdicts: dict = field(default_factory=dict)
    sessions: dict = field(default_factory=dict)
    approval: dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "state": self.state.value,
            "events": self.events,
            "findings": self.findings,
            "verdicts": self.verdicts,
            "sessions": self.sessions,
            "approval": self.approval,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> Workflow:
        return cls(
            incident_id=payload.get("incident_id", "INC-4021"),
            state=WorkflowState(payload.get("state", "IDLE")),
            events=payload.get("events", []),
            findings=payload.get("findings", {}),
            verdicts=payload.get("verdicts", {}),
            sessions=payload.get("sessions", {}),
            approval=payload.get("approval", {}),
            error=payload.get("error", ""),
        )

    def record(self, event: Event) -> dict:
        payload = event.to_dict()
        self.events.append(payload)
        return payload


_current: Workflow | None = None


def current() -> Workflow:
    """The live workflow, restored from disk if this process just started."""
    global _current
    with _lock:
        if _current is None:
            if STATE_PATH.exists():
                try:
                    _current = Workflow.from_dict(json.loads(STATE_PATH.read_text()))
                except (json.JSONDecodeError, ValueError):
                    _current = Workflow()
            else:
                _current = Workflow()
        return _current


def save() -> None:
    with _lock:
        workflow = current()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(workflow.to_dict(), indent=2))
        tmp.replace(STATE_PATH)


def reset() -> Workflow:
    """Clear the workflow for a fresh demo run."""
    global _current
    with _lock:
        _current = Workflow()
        save()
        return _current
