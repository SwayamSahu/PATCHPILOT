"""Deterministic telemetry for the simulated production environment.

Two properties matter here.

**Derived, not scripted.** Every metric is computed from the deployment ledger:
we ask "which revision was live at this instant, and was it faulty?" and the
numbers follow. Deploying a healthy revision makes the error rate fall because
the fault is gone, not because a recovery animation was triggered.

**Deterministic.** Jitter comes from a hash of the bucket timestamp rather than a
global RNG, so the same window always renders the same numbers, in any order, from
any process. The demo is reproducible.
"""

from __future__ import annotations

import hashlib
import math
import os
import traceback
from datetime import UTC, datetime, timedelta

from . import live, state

BASELINE_ERROR_RATE = 0.002
FAULTY_ERROR_RATE = 0.314
BASELINE_P95_MS = 420.0
FAULTY_P95_MS = 4200.0
DEGRADED_ERROR_RATE = 0.05
BASE_RPM = 180  # requests per minute the service normally handles

_RECOVERY_DECAY = 6.0
"""Exponential decay constant. Chosen so a full RECOVERY_SECONDS window takes the
error rate from the faulty level down to roughly the baseline."""


def _jitter(seed_text: str, spread: float) -> float:
    """Deterministic pseudo-noise in [-spread, +spread] derived from a label."""
    digest = hashlib.sha256(seed_text.encode()).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF  # [0, 1]
    return (unit * 2 - 1) * spread


def _active_deployment(at: datetime) -> tuple[dict, dict | None]:
    """Return (deployment live at `at`, the one before it)."""
    deployments = state.load().deployments
    active, previous = deployments[0], None
    for index, deployment in enumerate(deployments):
        if datetime.fromisoformat(deployment["deployed_at"]) <= at:
            active = deployment
            previous = deployments[index - 1] if index else None
    return active, previous


def _decay(at: datetime, deployment: dict, faulty_value: float, healthy_value: float) -> float:
    """Interpolate a metric while production drains after a recovery deploy."""
    elapsed = (at - datetime.fromisoformat(deployment["deployed_at"])).total_seconds()
    progress = min(1.0, max(0.0, elapsed / state.RECOVERY_SECONDS))
    return healthy_value + (faulty_value - healthy_value) * math.exp(-_RECOVERY_DECAY * progress)


def _sample(at: datetime) -> dict:
    """Compute one minute-bucket of telemetry for instant `at`."""
    active, previous = _active_deployment(at)
    recovering = active["healthy"] and previous is not None and not previous["healthy"]

    if not active["healthy"]:
        error_rate = FAULTY_ERROR_RATE + _jitter(f"err{at.isoformat()}", 0.018)
        p95 = FAULTY_P95_MS + _jitter(f"lat{at.isoformat()}", 260)
    elif recovering:
        error_rate = _decay(at, active, FAULTY_ERROR_RATE, BASELINE_ERROR_RATE)
        p95 = _decay(at, active, FAULTY_P95_MS, BASELINE_P95_MS)
    else:
        error_rate = BASELINE_ERROR_RATE + _jitter(f"err{at.isoformat()}", 0.0015)
        p95 = BASELINE_P95_MS + _jitter(f"lat{at.isoformat()}", 40)

    error_rate = max(0.0, error_rate)
    requests = BASE_RPM + int(_jitter(f"req{at.isoformat()}", 22))
    errors = round(requests * error_rate)
    return {
        "timestamp": at.isoformat(),
        "revision_id": active["revision_id"],
        "request_count": requests,
        "error_count": errors,
        "error_rate": round(error_rate, 4),
        "p50_latency_ms": round(p95 * 0.32, 1),
        "p95_latency_ms": round(p95, 1),
        "p99_latency_ms": round(p95 * 1.28, 1),
    }


def floor_to_minute(moment: datetime) -> datetime:
    """Snap an instant down to its minute boundary.

    Series windows must align to fixed boundaries. Seeding from a raw `now`
    carrying seconds and microseconds means two calls a moment apart share no
    bucket timestamps at all, so identical windows return entirely different
    points and the determinism guarantee is vacuous.
    """
    return moment.replace(second=0, microsecond=0)


def series(start: datetime, end: datetime, step_seconds: int = 60) -> list:
    """Return minute-bucketed telemetry across a window, aligned to the minute."""
    if end < start:
        raise ValueError("end must not be earlier than start")
    start, end = floor_to_minute(start), floor_to_minute(end)
    points, cursor = [], start
    step = timedelta(seconds=step_seconds)
    while cursor <= end:
        points.append(_sample(cursor))
        cursor += step
    return points


def sample_at(at: datetime) -> dict:
    """Telemetry for one specific instant. Useful for asserting the recovery curve."""
    return _sample(at)


def current() -> dict:
    """Telemetry for right now."""
    return _sample(datetime.now(UTC))


def health() -> dict:
    """The service-health summary the telemetry MCP server exposes."""
    now = _sample(datetime.now(UTC))
    active = state.load().current
    degraded = now["error_rate"] >= DEGRADED_ERROR_RATE
    return {
        "service": state.SERVICE_NAME,
        "status": "degraded" if degraded else "healthy",
        "error_rate": now["error_rate"],
        "p95_latency_ms": now["p95_latency_ms"],
        "request_count": now["request_count"],
        "deployed_revision": active["revision_id"],
        "deployed_at": active["deployed_at"],
        "checked_at": now["timestamp"],
    }


def _sample_cache_path():
    return state.STATE_PATH.parent / "error_sample.json"


def historical_error_sample() -> dict:
    """The exception that was raised while the service was faulty.

    Log entries from a faulty revision must keep reporting the exception that
    actually occurred then. Attaching a freshly captured sample to every
    historical entry meant that once the fix was deployed, the old 4c21 errors
    silently lost their ZeroDivisionError - erasing the very evidence the
    investigation depended on. The first real capture is therefore cached and
    reused for historical buckets.
    """
    import json

    cache = _sample_cache_path()
    if cache.exists():
        return json.loads(cache.read_text())

    sample = capture_error_sample()
    if sample.get("exception"):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(sample, indent=2))
    return sample


def capture_error_sample() -> dict:
    """Produce a real stack trace by actually running the faulty code path.

    We do not write traceback text by hand. The sample below is the genuine
    exception raised by the live ``compute_total`` for a zero-discount cart, so the
    file and line the Detective agent reads are the file and line that really fail.
    """
    try:
        checkout = live.module()
        checkout.compute_total(checkout.Cart(subtotal=100.0, discount=0.0))
    except ZeroDivisionError as exc:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        return {
            "exception": type(exc).__name__,
            "message": str(exc),
            "file": os.path.basename(frame.filename),
            "line": frame.lineno,
            "source": frame.line,
            "stacktrace": traceback.format_exc().strip(),
        }
    return {"exception": None, "message": "no error raised", "stacktrace": ""}
