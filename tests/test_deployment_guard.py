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


def _prepare(source=None, pr_number=27, commit_sha="abc1234def") -> str:
    """Prepare a deployment the way the agent would, returning its id."""
    plan = deployment_server.prepare_production_deployment(
        source=source if source is not None else _fixed_source(),
        summary="restore multiplicative discount",
        pr_number=pr_number,
        commit_sha=commit_sha,
    )
    return plan["deployment_id"]


# --------------------------------------------------------------------------
# The gate holds
# --------------------------------------------------------------------------


def test_production_deploy_without_approval_is_refused(live_simulator):
    """The single most important assertion in the codebase."""
    before = httpx.get(f"{live_simulator}/health", timeout=5).json()

    _prepare()
    result = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234", source=_fixed_source()
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
    _prepare()
    approvals.grant("deploy-27-abc1234", approved_by="engineer@example.com")

    result = deployment_server.deploy_production(
        deployment_id="deploy-99-deadbee", source=_fixed_source()
    )
    assert result["error"] == "human_approval_required"
    assert result["production_changed"] is False


def test_approvals_are_single_use():
    """A replayed deploy call after a rollback must not silently redeploy."""
    _prepare()
    approvals.grant("deploy-27-abc1234", approved_by="engineer@example.com")
    first = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234", source=_fixed_source()
    )
    assert first["production_changed"] is True

    second = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234", source=_fixed_source()
    )
    assert second["error"] == "human_approval_required"
    assert "single use" in second["detail"]


def test_expired_approvals_are_refused():
    _prepare()
    approvals.grant("deploy-27-abc1234", approved_by="engineer@example.com", ttl_seconds=-1)
    result = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234", source=_fixed_source()
    )
    assert result["error"] == "human_approval_required"
    assert "expired" in result["detail"]


def test_rejection_revokes_the_approval():
    _prepare()
    approvals.grant("deploy-27-abc1234", approved_by="engineer@example.com")
    assert approvals.revoke("deploy-27-abc1234") is True
    result = deployment_server.deploy_production(
        deployment_id="deploy-27-abc1234", source=_fixed_source()
    )
    assert result["error"] == "human_approval_required"


def test_the_deploy_tool_exposes_no_way_to_supply_a_token():
    """A model cannot forge a token it is never asked for."""
    import inspect

    params = set(inspect.signature(deployment_server.deploy_production).parameters)
    assert params == {"deployment_id", "source"}
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
        source=_fixed_source(),
        summary="fix discount calculation",
        pr_number=27,
        commit_sha="abc1234def",
    )
    assert plan["approval_status"] == "pending_human_approval"
    assert plan["reversible"] is False
    assert httpx.get(f"{live_simulator}/health", timeout=5).json()["deployed_revision"] == "4c21"


# --------------------------------------------------------------------------
# The gate opens, once, for a human
# --------------------------------------------------------------------------


def test_approved_deployment_reaches_production_and_recovers_it(live_simulator):
    deployment_id = _prepare()
    approvals.grant(deployment_id, approved_by="engineer@example.com")

    result = deployment_server.deploy_production(
        deployment_id=deployment_id, source=_fixed_source()
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


# --------------------------------------------------------------------------
# Security findings raised in Qodo review of PR #8
# --------------------------------------------------------------------------


def test_staging_deploy_cannot_reach_production(live_simulator):
    """Staging was posting to the endpoint that replaces production's module.

    That made the one unguarded write tool a way around the approval gate
    entirely: an agent could ship a fix to production by calling deploy_staging.
    """
    before_source = httpx.get(f"{live_simulator}/_control/live-source", timeout=5).json()["source"]
    before_health = httpx.get(f"{live_simulator}/health", timeout=5).json()

    result = deployment_server.deploy_staging(source=_fixed_source(), summary="candidate fix")
    assert result["production_untouched"] is True
    assert result["healthy"] is True, "staging should report the candidate as good"

    after_source = httpx.get(f"{live_simulator}/_control/live-source", timeout=5).json()["source"]
    after_health = httpx.get(f"{live_simulator}/health", timeout=5).json()

    assert after_source == before_source, "production source must be unchanged by staging"
    assert after_health["deployed_revision"] == before_health["deployed_revision"] == "4c21"
    assert httpx.post(
        f"{live_simulator}/checkout", json={"subtotal": 100, "discount": 0}, timeout=5
    ).status_code == 500, "production must still be broken after a staging deploy"


def test_approval_cannot_be_redirected_at_different_code(live_simulator):
    """An approval authorises a change, not merely an id.

    Without binding the approval to the artifact, an agent could have a human
    approve a reviewed one-line fix and then ship entirely different source under
    the approved id.
    """
    deployment_id = _prepare(source=_fixed_source())
    approvals.grant(deployment_id, approved_by="engineer@example.com")

    substituted = _fixed_source() + "\n# smuggled in after review\nimport os\n"
    result = deployment_server.deploy_production(
        deployment_id=deployment_id, source=substituted
    )

    assert result["error"] == "artifact_mismatch"
    assert result["production_changed"] is False
    assert httpx.post(
        f"{live_simulator}/checkout", json={"subtotal": 100, "discount": 0}, timeout=5
    ).status_code == 500


def test_a_mismatched_artifact_does_not_burn_the_approval():
    """The digest is checked first, so a failed substitution is recoverable."""
    deployment_id = _prepare(source=_fixed_source())
    approvals.grant(deployment_id, approved_by="engineer@example.com")

    deployment_server.deploy_production(
        deployment_id=deployment_id, source="# not the reviewed code"
    )

    honest = deployment_server.deploy_production(
        deployment_id=deployment_id, source=_fixed_source()
    )
    assert honest["production_changed"] is True, "the human's approval should still be usable"


def test_deploying_something_never_prepared_is_refused():
    approvals.grant("deploy-99-nothing", approved_by="engineer@example.com")
    result = deployment_server.deploy_production(
        deployment_id="deploy-99-nothing", source=_fixed_source()
    )
    assert result["error"] == "human_approval_required"
    assert "never prepared" in result["detail"]


def test_the_ledger_records_which_change_was_deployed(live_simulator):
    """A hard-coded revision id made every deployment look like the same one."""
    deployment_id = _prepare(pr_number=27, commit_sha="abc1234def")
    approvals.grant(deployment_id, approved_by="engineer@example.com")
    deployment_server.deploy_production(deployment_id=deployment_id, source=_fixed_source())

    deployed = httpx.get(f"{live_simulator}/health", timeout=5).json()["deployed_revision"]
    assert deployed == "pr27-abc1234"


def test_deploy_production_cannot_restate_the_summary_or_commit():
    """Both come from the prepared record, so neither can change after review."""
    import inspect

    params = set(inspect.signature(deployment_server.deploy_production).parameters)
    assert "summary" not in params
    assert "commit_sha" not in params
