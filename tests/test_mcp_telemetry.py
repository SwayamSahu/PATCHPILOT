"""Integration tests for the telemetry MCP server.

These call the tools the way the agent will — through the MCP server, against a
checkout-api running in another process over real HTTP. If the tools returned
canned data, these tests would still pass while the demo silently lied; so each
one asserts that the tool result agrees with what the service independently
reports.
"""

from __future__ import annotations

import httpx
import pytest

from mcp_servers.telemetry import server as telemetry_server


@pytest.fixture(autouse=True)
def _simulator(live_simulator):
    """Every test in this module needs production to be running."""
    return live_simulator


def test_health_matches_what_the_service_reports(live_simulator):
    tool = telemetry_server.get_service_health()
    direct = httpx.get(f"{live_simulator}/health", timeout=5).json()
    assert tool["status"] == direct["status"] == "degraded"
    assert tool["deployed_revision"] == "4c21"


def test_metrics_return_a_series_that_spans_the_requested_window():
    result = telemetry_server.get_metrics(minutes=30)
    assert result["window_minutes"] == 30
    assert len(result["series"]) >= 30
    point = result["series"][-1]
    assert {"error_rate", "p95_latency_ms", "request_count"} <= set(point)


def test_metrics_show_the_error_spike_beginning_at_the_faulty_deployment():
    """The evidence the Detective agent needs must actually be in the data."""
    series = telemetry_server.get_metrics(minutes=60)["series"]
    before = [p["error_rate"] for p in series if p["revision_id"] != "4c21"]
    after = [p["error_rate"] for p in series if p["revision_id"] == "4c21"]
    assert before and after, "window should straddle the 4c21 deployment"
    assert max(before) < 0.05, "production was healthy before 4c21"
    assert min(after) > 0.25, "production was broken after 4c21"


def test_query_logs_can_isolate_the_exception():
    result = telemetry_server.query_logs(query="ZeroDivisionError", level="ERROR", limit=10)
    assert result["count"] > 0
    entry = result["entries"][-1]
    assert entry["exception"] == "ZeroDivisionError"
    assert entry["file"] == "checkout.py"
    assert entry["line"] == 42


def test_deployment_history_identifies_the_faulty_revision():
    deployments = telemetry_server.get_recent_deployments()["deployments"]
    assert [d["revision_id"] for d in deployments] == ["4c18", "4c19", "4c20", "4c21"]
    faulty = [d["revision_id"] for d in deployments if not d["healthy"]]
    assert faulty == ["4c21"]


def test_incident_details_describe_an_open_sev1():
    incident = telemetry_server.get_incident_details("INC-4021")
    assert incident["severity"] == "SEV-1"
    assert incident["status"] == "open"


def test_unknown_incident_reports_not_found_rather_than_inventing_one():
    """A missing incident must not look like an outage of the monitoring system."""
    result = telemetry_server.get_incident_details("INC-0000")
    assert result["error"] == "not_found"
    assert result["status_code"] == 404


def test_error_samples_carry_a_real_stack_trace():
    sample = telemetry_server.get_error_samples()["sample"]
    assert sample["exception"] == "ZeroDivisionError"
    assert sample["file"] == "checkout.py"
    assert "Traceback" in sample["stacktrace"]


def test_tools_report_a_clear_error_when_production_is_unreachable(monkeypatch):
    """A broken tool must be distinguishable from a healthy service."""
    monkeypatch.setenv("SIMULATOR_URL", "http://127.0.0.1:1")
    result = telemetry_server.get_service_health()
    assert result["error"] == "production_environment_unreachable"
    assert "could not reach" in result["detail"]


def test_an_unknown_service_is_rejected_rather_than_silently_answered():
    """Returning checkout-api's telemetry under another name invents evidence."""
    for tool in (
        telemetry_server.get_service_health,
        telemetry_server.get_recent_deployments,
        telemetry_server.get_error_samples,
    ):
        result = tool(service="payments-api")
        assert result["error"] == "unknown_service"
        assert "checkout-api" in result["known_services"]

    assert telemetry_server.get_metrics(service="typo-api")["error"] == "unknown_service"
    assert telemetry_server.query_logs(service="typo-api")["error"] == "unknown_service"
