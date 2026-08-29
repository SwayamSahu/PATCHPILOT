"""The production safety boundary.

These are the most important tests in the project. Everything else determines
whether PatchPilot is useful; these determine whether it is safe.

The claim under test: an agent cannot deploy to production without a human, and
no argument, retry, forged value, or replay changes that.
"""

from __future__ import annotations

import httpx
import pytest

from mcp_servers.deployment import approvals
from mcp_servers.deployment import server as deployment_server
from mcp_servers.deployment.approvals import HumanApprovalRequired


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch, live_simulator):
    """Each test gets an empty approval store and a live production service."""
    monkeypatch.setattr(approvals, "STORE_PATH", tmp_path / "approvals.json")
    approvals.clear()
    httpx.post(f"{live_simulator}/_control/reset", timeout=5)
    return live_simulator


def _fixed_source() -> str:
    from app import live

    source = live.CANONICAL_PATH.read_text()
    return source.replace("cart.subtotal / cart.discount", "cart.subtotal * (1 - cart.discount)")


# --------------------------------------------------------------------------
# The gate holds
# --------------------------------------------------------------------------


def test_production_deploy_without_approval_is_refused(live_simulator):
    """The single most important assertion in the codebase."""
    before = httpx.get(f"{live_simulator}/health", timeout=5).json()

    result = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234",
        source=_fixed_source(),
        summary="fix discount calculation",
    )

    assert result["error"] == "human_approval_required"
    assert result["production_changed"] is False

    after = httpx.get(f"{live_simulator}/health", timeout=5).json()
    assert after["deployed_revision"] == before["deployed_revision"] == "4c21"
    assert httpx.post(
        f"{live_simulator}/checkout", json={"subtotal": 100, "discount": 0}, timeout=5
    ).status_code == 500, "production must still be running the faulty code"


def test_approval_for_one_deployment_does_not_authorise_another():
    """An approval is bound to the change a human actually looked at."""
    approvals.grant("deploy-27-abc1234", approved_by="engineer@example.com")

    result = deployment_server.deploy_production(
        deployment_id="deploy-99-deadbee",
        source=_fixed_source(),
        summary="something else entirely",
    )
    assert result["error"] == "human_approval_required"
    assert result["production_changed"] is False


def test_approvals_are_single_use():
    """A replayed deploy call after a rollback must not silently redeploy."""
    approvals.grant("deploy-27-abc1234", approved_by="engineer@example.com")
    first = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234", source=_fixed_source(), summary="fix"
    )
    assert first["production_changed"] is True

    second = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234", source=_fixed_source(), summary="fix"
    )
    assert second["error"] == "human_approval_required"
    assert "single use" in second["detail"]


def test_expired_approvals_are_refused():
    approvals.grant("deploy-27-abc1234", approved_by="engineer@example.com", ttl_seconds=-1)
    result = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234", source=_fixed_source(), summary="fix"
    )
    assert result["error"] == "human_approval_required"
    assert "expired" in result["detail"]


def test_rejection_revokes_the_approval():
    approvals.grant("deploy-27-abc1234", approved_by="engineer@example.com")
    assert approvals.revoke("deploy-27-abc1234") is True
    result = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234", source=_fixed_source(), summary="fix"
    )
    assert result["error"] == "human_approval_required"


def test_the_deploy_tool_exposes_no_way_to_supply_a_token():
    """A model cannot forge a token it is never asked for."""
    import inspect

    params = set(inspect.signature(deployment_server.deploy_production).parameters)
    assert params == {"deployment_id", "source", "summary", "commit_sha"}
    assert not any("token" in p or "approv" in p for p in params)


def test_no_mcp_tool_can_grant_an_approval():
    """The approval path must not be reachable from the agent's tool surface."""
    import asyncio

    names = {t.name for t in asyncio.run(deployment_server.server.list_tools())}
    assert names == {
        "deploy_staging",
        "get_staging_health",
        "prepare_production_deployment",
        "deploy_production",
        "get_production_health",
    }
    assert not any("approv" in n or "grant" in n or "token" in n for n in names)


def test_preparing_a_deployment_does_not_deploy_anything(live_simulator):
    plan = deployment_server.prepare_production_deployment(
        summary="fix discount calculation", pr_number=27, commit_sha="abc1234def"
    )
    assert plan["approval_status"] == "pending_human_approval"
    assert plan["reversible"] is False
    assert httpx.get(f"{live_simulator}/health", timeout=5).json()["deployed_revision"] == "4c21"


# --------------------------------------------------------------------------
# The gate opens, once, for a human
# --------------------------------------------------------------------------


def test_approved_deployment_reaches_production_and_recovers_it(live_simulator):
    plan = deployment_server.prepare_production_deployment(
        summary="restore multiplicative discount", pr_number=27, commit_sha="abc1234def"
    )
    approvals.grant(plan["deployment_id"], approved_by="engineer@example.com")

    result = deployment_server.deploy_production(
        deployment_id=plan["deployment_id"],
        source=_fixed_source(),
        summary="restore multiplicative discount",
    )

    assert result["status"] == "deployed"
    assert result["production_changed"] is True
    assert result["approved_by"] == "engineer@example.com"

    order = httpx.post(
        f"{live_simulator}/checkout", json={"subtotal": 100, "discount": 0}, timeout=5
    )
    assert order.status_code == 200, "production should have stopped failing"
    assert order.json()["order"]["total"] == 100.0


def test_staging_needs_no_approval(live_simulator):
    """Reversible actions stay unblocked; the gate is targeted, not blanket."""
    result = deployment_server.deploy_staging(source=_fixed_source(), summary="candidate fix")
    assert "error" not in result
    assert deployment_server.get_staging_health()["status"] in {"healthy", "degraded"}


# --------------------------------------------------------------------------
# Store-level invariants
# --------------------------------------------------------------------------


def test_consume_raises_the_named_exception_when_unapproved():
    with pytest.raises(HumanApprovalRequired):
        approvals.consume("never-approved")


def test_granted_tokens_are_unguessable_and_distinct():
    a = approvals.grant("deploy-a", approved_by="x")
    b = approvals.grant("deploy-b", approved_by="x")
    assert a.token != b.token
    assert len(a.token) >= 32
