"""Telemetry MCP server — the agent's window onto production.

Every tool here reaches the running checkout-api over HTTP and returns structured
JSON derived from real service state. Nothing is fabricated for the model's
benefit: if production is healthy, these tools say so.

This server is **read-only by construction**. It exposes no way to change
anything, which is why the Detective and Validator agents can be handed it without
qualification.
"""

from __future__ import annotations

import os
import sys
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mcp_servers.common import simulator  # noqa: E402
from mcp_servers.common.simulator import SimulatorError, SimulatorUnavailable  # noqa: E402

server = MCPServer(
    name="patchpilot-telemetry",
    instructions=(
        "Read-only production observability for the checkout-api service. "
        "Use these tools to gather evidence about an incident: current health, "
        "metric history, application logs, deployment history, and captured "
        "stack traces. None of these tools change anything."
    ),
)

KNOWN_SERVICES = ("checkout-api",)

Service = Annotated[str, Field(description="Service name, e.g. 'checkout-api'.")]


def _check_service(service: str):
    """Reject a service this environment does not monitor.

    Every tool took a service argument and then ignored it, so a typo or a
    genuinely different service silently returned checkout-api's telemetry. An
    investigation could then attribute one service's health to another and be
    confidently wrong. Unknown names now fail loudly.
    """
    if service and service not in KNOWN_SERVICES:
        return {
            "error": "unknown_service",
            "detail": f"{service!r} is not monitored here. Known services: "
            + ", ".join(KNOWN_SERVICES),
            "known_services": list(KNOWN_SERVICES),
        }
    return None


def _guard(call):
    """Turn transport failures into a clear tool-level error.

    An agent that gets a plain exception cannot tell a broken tool from a healthy
    service. Being explicit lets it retry or report rather than guess.
    """
    try:
        return call()
    except SimulatorUnavailable as exc:
        return {"error": "production_environment_unreachable", "detail": str(exc)}
    except SimulatorError as exc:
        kind = "not_found" if exc.status_code == 404 else "upstream_error"
        return {"error": kind, "status_code": exc.status_code, "detail": exc.detail}


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Current health of a service: status, error rate, p95 latency, "
    "and the revision currently deployed."
)
def get_service_health(service: Service = "checkout-api") -> dict:
    return _check_service(service) or _guard(lambda: simulator.get("/health"))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Minute-bucketed metric history for a service: request_count, "
    "error_count, error_rate, and p50/p95/p99 latency. Use this to find when a "
    "problem started."
)
def get_metrics(
    service: Service = "checkout-api",
    minutes: Annotated[
        int, Field(description="How far back to look, in minutes.", ge=1, le=720)
    ] = 45,
) -> dict:
    unavailable = _check_service(service)
    if unavailable:
        return unavailable
    raw = _guard(lambda: simulator.get("/metrics", params={"minutes": minutes}))
    return raw if "error" in raw else _summarise(raw)


def _summarise(raw: dict) -> dict:
    """Compact a minute-by-minute series into something worth reading.

    Returning sixty raw buckets is a poor tool result twice over. It buries the
    finding that matters - error rate changed at a particular moment - under
    arithmetic the model then has to do itself, and it consumes enough context to
    measurably slow every later step in the investigation.

    So the per-revision breakdown is computed here, where it is exact, and the
    series is downsampled to a readable shape. The comparison an investigation
    actually needs is then a single glance.
    """
    series = raw.get("series", [])
    if not series:
        return raw

    by_revision: dict = {}
    for point in series:
        bucket = by_revision.setdefault(
            point["revision_id"],
            {"revision_id": point["revision_id"], "samples": 0, "error_rates": [], "p95": []},
        )
        bucket["samples"] += 1
        bucket["error_rates"].append(point["error_rate"])
        bucket["p95"].append(point["p95_latency_ms"])

    breakdown = []
    for bucket in by_revision.values():
        rates, latencies = bucket["error_rates"], bucket["p95"]
        breakdown.append({
            "revision_id": bucket["revision_id"],
            "samples": bucket["samples"],
            "avg_error_rate": round(sum(rates) / len(rates), 4),
            "peak_error_rate": round(max(rates), 4),
            "avg_p95_latency_ms": round(sum(latencies) / len(latencies), 1),
        })

    step = max(1, len(series) // 12)
    return {
        "service": raw.get("service"),
        "window_minutes": raw.get("window_minutes"),
        "current": raw.get("current"),
        "by_revision": breakdown,
        "series": series[::step][-12:],
        "note": "by_revision compares each deployed revision over this window; "
        "series is downsampled to 12 points.",
    }


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Search application logs. Filter by free text and/or level "
    "(INFO, ERROR). Error entries include the exception, file, line, and full "
    "stack trace."
)
def query_logs(
    service: Service = "checkout-api",
    query: Annotated[str, Field(description="Free-text filter, e.g. an exception name.")] = "",
    level: Annotated[str, Field(description="Log level filter: INFO or ERROR.")] = "",
    limit: Annotated[int, Field(description="Maximum entries to return.", ge=1, le=200)] = 50,
) -> dict:
    params = {"limit": limit}
    if query:
        params["query"] = query
    if level:
        params["level"] = level
    return _check_service(service) or _guard(lambda: simulator.get("/logs", params=params))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Deployment history for a service, oldest first. Each record has "
    "a revision id, summary, author, timestamp, and whether it proved healthy. "
    "Correlate these timestamps against when metrics degraded."
)
def get_recent_deployments(service: Service = "checkout-api") -> dict:
    return _check_service(service) or _guard(lambda: simulator.get("/deployments"))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Full details of a specific incident, including the breached threshold.")
def get_incident_details(
    incident_id: Annotated[str, Field(description="Incident id, e.g. 'INC-4021'.")] = "INC-4021",
) -> dict:
    return _guard(lambda: simulator.get(f"/incidents/{incident_id}"))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="A captured stack trace from the failing code path, including the "
    "exception type, source file, line number, and the offending source line."
)
def get_error_samples(service: Service = "checkout-api") -> dict:
    return _check_service(service) or _guard(lambda: simulator.get("/errors/samples"))


def main() -> None:
    import uvicorn

    port = int(os.environ.get("MCP_TELEMETRY_PORT", "8101"))
    uvicorn.run(server.streamable_http_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
