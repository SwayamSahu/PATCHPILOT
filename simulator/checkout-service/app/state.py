"""Persistent state for the simulated production environment.

State lives on disk so the simulator survives a restart mid-demo and so
`scripts/reset-demo.sh` can return the world to a known-good starting point.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .revisions import BASELINE_REVISION_ID, get_revision

STATE_PATH = Path(
    os.environ.get(
        "SIMULATOR_STATE_PATH",
        Path(__file__).resolve().parents[2] / ".state" / "state.json",
    )
)

SERVICE_NAME = "checkout-api"

RECOVERY_SECONDS = float(os.environ.get("SIMULATOR_RECOVERY_SECONDS", "24"))
"""How long production takes to drain and fully recover after a healthy deploy.

Real services recover over minutes. The demo compresses that so the curve is
watchable inside a three-minute video; the curve is still computed from elapsed
time against the deployment rather than scripted frame by frame.
"""


@dataclass
class DeploymentRecord:
    """One entry in the deployment ledger."""

    revision_id: str
    summary: str
    author: str
    deployed_at: str
    healthy: bool
    commit_sha: str | None = None
    deployed_by: str = "ci"


@dataclass
class SimulatorState:
    """Everything the simulated production environment remembers."""

    service: str = SERVICE_NAME
    deployments: list = field(default_factory=list)
    started_at: str = ""
    request_log: list = field(default_factory=list)

    @property
    def current(self) -> dict:
        return self.deployments[-1]

    @property
    def current_revision_id(self) -> str:
        return self.current["revision_id"]

    def deployed_at(self) -> datetime:
        return datetime.fromisoformat(self.current["deployed_at"])


_lock = threading.RLock()
_state = None


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_state() -> SimulatorState:
    """Build the opening scene: four deployments, the last one faulty."""
    now = _now()
    state = SimulatorState(started_at=now.isoformat())
    for revision_id in ("4c18", "4c19", "4c20", BASELINE_REVISION_ID):
        revision = get_revision(revision_id)
        deployed_at = now - timedelta(minutes=revision.deployed_at_offset_min)
        state.deployments.append(
            asdict(
                DeploymentRecord(
                    revision_id=revision.id,
                    summary=revision.summary,
                    author=revision.author,
                    deployed_at=deployed_at.isoformat(),
                    healthy=revision.healthy,
                    commit_sha=revision.commit_sha,
                )
            )
        )
    return state


def load() -> SimulatorState:
    """Return the live state, seeding and persisting it on first use."""
    global _state
    with _lock:
        if _state is not None:
            return _state
        if STATE_PATH.exists():
            _state = SimulatorState(**json.loads(STATE_PATH.read_text()))
        else:
            _state = _seed_state()
            _persist(_state)
        return _state


def save(state: SimulatorState) -> None:
    with _lock:
        _persist(state)


def _persist(state: SimulatorState) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2))
    tmp.replace(STATE_PATH)  # atomic: a crash mid-write cannot corrupt state


def reset() -> SimulatorState:
    """Return the world to the opening scene of the demo."""
    global _state
    with _lock:
        _state = _seed_state()
        _persist(_state)
        return _state


def record_deployment(
    revision_id: str,
    summary: str,
    author: str,
    healthy: bool,
    commit_sha=None,
    deployed_by: str = "ci",
) -> DeploymentRecord:
    """Append a deployment, changing what the service actually runs."""
    with _lock:
        state = load()
        record = DeploymentRecord(
            revision_id=revision_id,
            summary=summary,
            author=author,
            deployed_at=_now().isoformat(),
            healthy=healthy,
            commit_sha=commit_sha,
            deployed_by=deployed_by,
        )
        state.deployments.append(asdict(record))
        _persist(state)
        return record


def seconds_since_deploy() -> float:
    return max(0.0, (_now() - load().deployed_at()).total_seconds())
