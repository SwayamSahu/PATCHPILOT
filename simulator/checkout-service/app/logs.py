"""Deterministic application logs for the simulated production environment.

Log lines are generated from the same deployment ledger that drives the metrics,
so logs and metrics always agree with each other — an incident investigation that
correlates them is correlating real signals, not two independent fictions.

Error entries carry the genuine stack trace captured by
`telemetry.capture_error_sample`, so the file and line an agent reads in the logs
are the file and line that actually raise.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from . import state, telemetry

_INFO_TEMPLATES = (
    "checkout completed order_id={oid} total={total}",
    "cart priced order_id={oid} items={items}",
    "payment authorised order_id={oid}",
)


def _revision_was_healthy(revision_id: str) -> bool:
    """Whether the named revision was healthy, per the deployment ledger."""
    for deployment in state.load().deployments:
        if deployment["revision_id"] == revision_id:
            return bool(deployment["healthy"])
    return True


def _order_id(at: datetime, index: int) -> str:
    seed = f"{at.isoformat()}:{index}".encode()
    return "ord_" + hashlib.sha256(seed).hexdigest()[:10]


def generate(minutes: int = 45, max_entries: int = 400) -> list:
    """Build the recent log stream, newest last."""
    now = datetime.now(UTC)
    start = now - timedelta(minutes=minutes)
    entries = []

    for point in telemetry.series(start, now, step_seconds=60):
        at = datetime.fromisoformat(point["timestamp"])
        revision = point["revision_id"]

        # Two INFO lines per minute keep the stream readable rather than exhaustive.
        for index in range(2):
            stamp = at + timedelta(seconds=7 + index * 19)
            template = _INFO_TEMPLATES[index % len(_INFO_TEMPLATES)]
            entries.append(
                {
                    "timestamp": stamp.isoformat(),
                    "level": "INFO",
                    "service": state.SERVICE_NAME,
                    "revision_id": revision,
                    "message": template.format(
                        oid=_order_id(at, index),
                        total=round(84.0 + index * 3.5, 2),
                        items=2 + index,
                    ),
                }
            )

        # Errors are attributed to the revision that actually raised them. A
        # healthy revision's residual errors are ordinary failures, not the
        # ZeroDivisionError from the faulty window; labelling them all the same
        # way would invent evidence for a defect that was not running.
        faulty_window = not _revision_was_healthy(revision)
        error_sample = telemetry.historical_error_sample() if faulty_window else {}

        # Error volume tracks the measured error count, capped so a minute of a
        # 31% error rate does not drown the stream.
        for index in range(min(point["error_count"], 3)):
            stamp = at + timedelta(seconds=11 + index * 13)
            entries.append(
                {
                    "timestamp": stamp.isoformat(),
                    "level": "ERROR",
                    "service": state.SERVICE_NAME,
                    "revision_id": revision,
                    "message": f"checkout failed order_id={_order_id(at, 90 + index)}",
                    "exception": error_sample.get("exception") if faulty_window else None,
                    "exception_message": error_sample.get("message") if faulty_window else None,
                    "file": error_sample.get("file") if faulty_window else None,
                    "line": error_sample.get("line") if faulty_window else None,
                    "stacktrace": error_sample.get("stacktrace") if faulty_window else None,
                }
            )

    # Bucket offsets push some entries past the final bucket's minute boundary.
    # A log stream containing events from the future is obviously wrong and would
    # undermine any timing correlation drawn from it.
    horizon = now.isoformat()
    entries = [entry for entry in entries if entry["timestamp"] <= horizon]
    entries.sort(key=lambda entry: entry["timestamp"])
    return entries[-max_entries:]


def query(text: str | None = None, level: str | None = None, limit: int = 100) -> list:
    """Filter the log stream by free text and/or level."""
    entries = generate()
    if level:
        wanted = level.upper()
        entries = [e for e in entries if e["level"] == wanted]
    if text:
        needle = text.lower()
        entries = [
            e
            for e in entries
            if any(needle in str(value).lower() for value in e.values())
        ]
    return entries[-limit:]


def format_line(entry: dict) -> str:
    """Render a log entry the way it appears in a terminal tail."""
    clock = datetime.fromisoformat(entry["timestamp"]).strftime("%H:%M:%S")
    head = f"[{clock}] {entry['level']} {entry['service']}"
    if entry["level"] == "ERROR":
        return f"{head}\n{entry.get('exception')}\n{entry.get('file')}:{entry.get('line')}"
    return f"{head} {entry['message']}"
