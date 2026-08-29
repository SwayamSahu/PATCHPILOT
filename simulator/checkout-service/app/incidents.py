"""Incident records for the simulated production environment.

An incident is derived from live telemetry rather than stored as fixed text, so
the numbers an agent reads in the incident brief are the same numbers it will find
when it queries the metrics itself. If production recovers, the incident reflects
that too.
"""

from __future__ import annotations

from . import state, telemetry

INCIDENT_ID = "INC-4021"


def get(incident_id: str = INCIDENT_ID) -> dict:
    """Return the incident record, or raise KeyError if unknown."""
    if incident_id != INCIDENT_ID:
        raise KeyError(f"unknown incident {incident_id!r}; known: {INCIDENT_ID}")

    health = telemetry.health()
    current = state.load().current
    resolved = health["status"] == "healthy"

    return {
        "incident_id": INCIDENT_ID,
        "title": "Checkout failure rate above production threshold",
        "service": state.SERVICE_NAME,
        "severity": "SEV-1",
        "status": "resolved" if resolved else "open",
        "opened_at": _fault_began_at(),
        "threshold": {
            "metric": "error_rate",
            "operator": ">",
            "value": telemetry.DEGRADED_ERROR_RATE,
        },
        "observed": {
            "error_rate": health["error_rate"],
            "p95_latency_ms": health["p95_latency_ms"],
        },
        "deployed_revision": current["revision_id"],
        "description": (
            "Checkout failures have exceeded the production threshold. "
            "Investigate and resolve the incident."
        ),
    }


def _fault_began_at() -> str:
    """When the fault was introduced, not when we last deployed something.

    Reading the newest deployment meant that deploying the fix rewrote the same
    incident's apparent start time to the moment of the fix, which would make any
    correlation an agent had already drawn look wrong.
    """
    deployments = state.load().deployments
    for deployment in deployments:
        if not deployment["healthy"]:
            return deployment["deployed_at"]
    return deployments[-1]["deployed_at"]


def list_all() -> list:
    return [get()]
