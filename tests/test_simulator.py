"""Tests for the simulated production environment.

These assert the two properties the demo depends on: the world is deterministic,
and recovery after a deployment is genuine rather than cosmetic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A checkout-api instance with isolated on-disk state."""
    monkeypatch.setenv("SIMULATOR_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SIMULATOR_RECOVERY_SECONDS", "24")
    # Import after the env is set so the state path is picked up at module load.
    for mod in list(m for m in list(__import__("sys").modules) if m.startswith("app")):
        del __import__("sys").modules[mod]
    from app.main import app

    with TestClient(app) as c:
        c.post("/_control/reset")
        yield c


# --------------------------------------------------------------------------
# The opening scene
# --------------------------------------------------------------------------


def test_starts_broken_on_4c21(client):
    health = client.get("/health").json()
    assert health["deployed_revision"] == "4c21"
    assert health["status"] == "degraded"
    assert health["error_rate"] > 0.25


def test_deployment_ledger_has_four_entries_ending_in_the_faulty_one(client):
    deployments = client.get("/deployments").json()["deployments"]
    assert [d["revision_id"] for d in deployments] == ["4c18", "4c19", "4c20", "4c21"]
    assert [d["healthy"] for d in deployments] == [True, True, True, False]


def test_incident_is_open_and_reports_observed_error_rate(client):
    incident = client.get("/incidents/INC-4021").json()
    assert incident["status"] == "open"
    assert incident["severity"] == "SEV-1"
    assert incident["observed"]["error_rate"] > incident["threshold"]["value"]


def test_unknown_incident_is_404(client):
    assert client.get("/incidents/INC-9999").status_code == 404


# --------------------------------------------------------------------------
# The defect
# --------------------------------------------------------------------------


def test_zero_discount_checkout_returns_500(client):
    response = client.post("/checkout", json={"subtotal": 100, "discount": 0})
    assert response.status_code == 500
    assert response.json()["detail"]["exception"] == "ZeroDivisionError"


def test_nonzero_discount_also_prices_incorrectly_under_the_bug(client):
    """25% off $100 should be $75; the faulty division yields $400."""
    order = client.post("/checkout", json={"subtotal": 100, "discount": 0.25}).json()["order"]
    total = order["total"]
    assert total == 400.0


def test_error_sample_points_at_the_real_failing_line(client):
    sample = client.get("/errors/samples").json()["sample"]
    assert sample["exception"] == "ZeroDivisionError"
    assert sample["file"] == "checkout.py"
    assert "cart.subtotal / cart.discount" in sample["source"]


def test_error_logs_carry_the_same_exception_as_the_metrics(client):
    entries = client.get("/logs", params={"level": "ERROR", "limit": 5}).json()["entries"]
    assert entries, "expected error logs while production is broken"
    assert all(e["exception"] == "ZeroDivisionError" for e in entries)
    assert all(e["file"] == "checkout.py" for e in entries)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_metrics_for_the_same_window_are_stable_across_calls(client):
    first = client.get("/metrics", params={"minutes": 20}).json()["series"]
    second = client.get("/metrics", params={"minutes": 20}).json()["series"]
    overlap = {p["timestamp"]: p for p in first}
    for point in second:
        if point["timestamp"] in overlap:
            assert point == overlap[point["timestamp"]], "same instant returned different telemetry"


def test_metrics_window_is_rejected_when_inverted():
    from app import telemetry

    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        telemetry.series(now, now - timedelta(minutes=5))


# --------------------------------------------------------------------------
# Recovery is real
# --------------------------------------------------------------------------


FIXED_SOURCE_MARKER = "cart.subtotal * (1 - cart.discount)"


def _fixed_source(client) -> str:
    """The versioned source with the discount defect corrected."""
    from app import live

    source = live.CANONICAL_PATH.read_text()
    assert "cart.subtotal / cart.discount" in source, "expected the faulty source in git"
    return source.replace("cart.subtotal / cart.discount", FIXED_SOURCE_MARKER)


def _deploy_fix(client):
    return client.post(
        "/_control/deploy",
        json={
            "revision_id": "4c22",
            "summary": "checkout: restore multiplicative discount",
            "healthy": True,
            "source": _fixed_source(client),
            "deployed_by": "patchpilot",
        },
    )


def test_deploying_the_fix_makes_checkout_succeed(client):
    _deploy_fix(client)
    response = client.post("/checkout", json={"subtotal": 100, "discount": 0})
    assert response.status_code == 200
    assert response.json()["order"]["total"] == 100.0

    discounted = client.post("/checkout", json={"subtotal": 100, "discount": 0.25})
    assert discounted.json()["order"]["total"] == 75.0


def test_error_rate_decays_monotonically_after_a_healthy_deployment(client):
    """Recovery is a curve computed from elapsed time, not a scripted animation."""
    from app import state, telemetry

    before = client.get("/health").json()["error_rate"]
    _deploy_fix(client)

    deployed_at = state.load().deployed_at()
    window = state.RECOVERY_SECONDS
    rates = [
        telemetry.sample_at(deployed_at + timedelta(seconds=window * f))["error_rate"]
        for f in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]

    assert rates == sorted(rates, reverse=True), f"error rate should fall, got {rates}"
    assert rates[0] >= before * 0.9, "recovery should start from roughly the incident level"
    assert rates[-1] < telemetry.DEGRADED_ERROR_RATE, "should end below the alert threshold"
    assert rates[-1] < rates[0] / 10, "recovery should be real, not cosmetic"


def test_deploying_never_mutates_the_versioned_source(client):
    """Production runs a working copy; the file under git must stay untouched."""
    from app import live

    before = live.CANONICAL_PATH.read_text()
    _deploy_fix(client)
    assert live.CANONICAL_PATH.read_text() == before
    assert FIXED_SOURCE_MARKER in client.get("/_control/live-source").json()["source"]


def test_reset_restores_the_broken_world(client):
    _deploy_fix(client)
    assert client.post("/checkout", json={"subtotal": 100, "discount": 0}).status_code == 200

    client.post("/_control/reset")
    assert client.get("/health").json()["deployed_revision"] == "4c21"
    assert client.post("/checkout", json={"subtotal": 100, "discount": 0}).status_code == 500
