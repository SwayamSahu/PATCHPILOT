"""checkout-api — the simulated production service PatchPilot investigates.

This is a real HTTP service with real state. The MCP servers query it over the
network, and a deployment genuinely changes what code the service runs, which is
what makes post-fix recovery observable instead of asserted.

Endpoints under `/_control` are the deployment plane. They stand in for a CD
system and are never exposed to the agent directly — only the deployment MCP
server calls them, and only after the human approval gate has been cleared.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import incidents, live, logs, revisions, state, telemetry

logger = logging.getLogger("checkout-api")

_DEPLOY_LOCK = threading.RLock()
"""Serialises publishing code and recording the ledger entry as one operation."""

app = FastAPI(
    title="checkout-api",
    version="1.0.0",
    description="Simulated production checkout service for the PatchPilot demo.",
)


class CheckoutRequest(BaseModel):
    subtotal: float = Field(..., ge=0, description="Cart subtotal in dollars.")
    discount: float = Field(0.0, ge=0.0, lt=1.0, description="Fractional discount.")
    currency: str = Field("USD", min_length=3, max_length=3)


class DeployRequest(BaseModel):
    """A deployment request.

    Note there is no `healthy` field. Health is *verified* against the code that
    ends up running, never declared by the caller: a request that could assert its
    own healthiness could make telemetry report recovery while production kept
    serving the broken module, which would hollow out the one guarantee this
    simulator exists to provide.
    """

    revision_id: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    source: str | None = Field(None, description="New checkout.py contents to apply.")
    commit_sha: str | None = None
    deployed_by: str = "ci"


@app.get("/health")
def get_health() -> dict:
    """Live service health, derived from current telemetry."""
    return telemetry.health()


@app.post("/checkout")
def post_checkout(request: CheckoutRequest) -> dict:
    """Price a cart. This is the endpoint that fails under revision 4c21."""
    checkout = live.module()
    cart = checkout.Cart(
        subtotal=request.subtotal,
        discount=request.discount,
        currency=request.currency,
    )
    try:
        return {"status": "ok", "order": checkout.checkout(cart)}
    except checkout.CheckoutError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # surfaced as a 500, exactly as production would
        logger.exception("checkout failed")
        raise HTTPException(
            status_code=500,
            detail={"exception": type(exc).__name__, "message": str(exc)},
        ) from exc


@app.get("/metrics")
def get_metrics(minutes: int = Query(45, ge=1, le=720)) -> dict:
    """Minute-bucketed telemetry for the last `minutes` minutes."""
    now = datetime.now(UTC)
    points = telemetry.series(now - timedelta(minutes=minutes), now)
    return {
        "service": state.SERVICE_NAME,
        "window_minutes": minutes,
        "current": telemetry.current(),
        "series": points,
    }


@app.get("/logs")
def get_logs(
    query: str | None = None,
    level: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    entries = logs.query(text=query, level=level, limit=limit)
    return {"service": state.SERVICE_NAME, "count": len(entries), "entries": entries}


@app.get("/deployments")
def get_deployments() -> dict:
    """The deployment ledger, newest last."""
    return {"service": state.SERVICE_NAME, "deployments": state.load().deployments}


@app.get("/errors/samples")
def get_error_samples() -> dict:
    """A genuine captured stack trace from the current code path."""
    return {"service": state.SERVICE_NAME, "sample": telemetry.capture_error_sample()}


@app.get("/incidents")
def get_incidents() -> dict:
    return {"incidents": incidents.list_all()}


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict:
    try:
        return incidents.get(incident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Deployment plane
# --------------------------------------------------------------------------


@app.post("/_control/reset")
def control_reset() -> dict:
    """Return production to the opening scene: broken, running 4c21."""
    _restore_baseline_source()
    fresh = state.reset()
    return {"status": "reset", "deployed_revision": fresh.current_revision_id}


@app.post("/_control/deploy")
def control_deploy(request: DeployRequest) -> dict:
    """Deploy a revision, replacing the running checkout implementation.

    Publishing the code and recording the ledger entry happen under a single lock.
    Holding two separate locks let concurrent deploys interleave and leave the
    newest ledger entry describing a different revision than the one actually
    running.
    """
    with _DEPLOY_LOCK:
        if request.source is not None:
            try:
                _apply_source(request.source)
            except live.InvalidDeployment as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        healthy = live.verify_healthy()
        record = state.record_deployment(
            revision_id=request.revision_id,
            summary=request.summary,
            author="patchpilot" if request.deployed_by != "ci" else "ci",
            healthy=healthy,
            commit_sha=request.commit_sha,
            deployed_by=request.deployed_by,
        )

    logger.info("deployed revision %s (verified healthy=%s)", record.revision_id, healthy)
    return {"status": "deployed", "deployment": record.__dict__, "health": telemetry.health()}


@app.get("/_control/state")
def control_state() -> dict:
    return {
        "deployed_revision": state.load().current_revision_id,
        "seconds_since_deploy": round(state.seconds_since_deploy(), 1),
        "known_revisions": [r.id for r in revisions.all_revisions()],
    }


def _apply_source(source: str) -> None:
    """Move new source into the live working copy. This is what "deploy" means."""
    live.deploy_source(source)


def _restore_baseline_source() -> None:
    """Roll production back to the versioned source, faults and all."""
    live.reset()


@app.get("/_control/live-source")
def control_live_source() -> dict:
    """The source production is currently executing."""
    return {"source": live.live_source()}
