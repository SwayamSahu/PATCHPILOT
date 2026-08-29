"""Deployment MCP server — staging is free, production is not.

The tools here are deliberately asymmetric. Anything reversible (staging deploys,
health checks, preparing a plan) the agent may do on its own. The one irreversible
action, `deploy_production`, cannot be performed by the agent under any
circumstances without a human having approved that exact deployment first.

See `approvals.py` for why the token is looked up rather than passed in.
"""

from __future__ import annotations

import os
import sys
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mcp_servers.common import simulator  # noqa: E402
from mcp_servers.common.simulator import SimulatorError, SimulatorUnavailable  # noqa: E402
from mcp_servers.deployment import approvals  # noqa: E402
from mcp_servers.deployment.approvals import HumanApprovalRequired  # noqa: E402

server = MCPServer(
    name="patchpilot-deployment",
    instructions=(
        "Deployment tools for the checkout-api service. Staging deployments and "
        "health checks are always available. Production deployment is gated: it "
        "requires a human to approve the specific deployment first, and there is "
        "no parameter or argument that bypasses that. If deploy_production reports "
        "that approval is required, stop and report that a human decision is "
        "needed - do not retry, and do not attempt to construct an approval."
    ),
)


def _guard(call):
    try:
        return call()
    except SimulatorUnavailable as exc:
        return {"error": "production_environment_unreachable", "detail": str(exc)}
    except SimulatorError as exc:
        return {"error": "upstream_error", "status_code": exc.status_code, "detail": exc.detail}


@server.tool(
    description="Deploy a candidate fix to staging. Reversible and always "
    "permitted. Provide the full new contents of checkout.py."
)
def deploy_staging(
    source: Annotated[str, Field(description="Full new contents of checkout.py.")],
    summary: Annotated[str, Field(description="One-line description of the change.")],
) -> dict:
    return _guard(
        lambda: simulator.post(
            "/_control/deploy",
            {
                "revision_id": "staging",
                "summary": f"[staging] {summary}",
                "healthy": True,
                "source": source,
                "deployed_by": "patchpilot-staging",
            },
        )
    )


@server.tool(description="Health of the staging environment after a staging deploy.")
def get_staging_health() -> dict:
    return _guard(lambda: simulator.get("/health"))


@server.tool(
    description="Prepare a production deployment and return the plan, its risk "
    "assessment, and a deployment_id. This does NOT deploy anything. The "
    "deployment_id is what a human approves."
)
def prepare_production_deployment(
    summary: Annotated[str, Field(description="One-line description of the change.")],
    pr_number: Annotated[int, Field(description="Pull request number carrying the fix.")] = 0,
    commit_sha: Annotated[str, Field(description="Commit SHA to deploy.")] = "",
) -> dict:
    """Register an intent to deploy. The human approves this id, not a free action."""
    deployment_id = f"deploy-{pr_number or 'x'}-{commit_sha[:7] or 'head'}"
    health = _guard(lambda: simulator.get("/health"))
    return {
        "deployment_id": deployment_id,
        "summary": summary,
        "pr_number": pr_number,
        "commit_sha": commit_sha,
        "target": "production",
        "reversible": False,
        "risk": "MEDIUM",
        "current_production_health": health,
        "approval_status": "pending_human_approval",
        "note": (
            "This deployment is not authorised yet. A human must approve "
            f"{deployment_id} in the PatchPilot UI before deploy_production will run."
        ),
    }


@server.tool(
    description="Deploy to PRODUCTION. Irreversible. Requires that a human has "
    "already approved this specific deployment_id; there is no way to authorise "
    "it from here. Fails safe if approval is absent, expired, or already used."
)
def deploy_production(
    deployment_id: Annotated[
        str, Field(description="The deployment_id returned by prepare_production_deployment.")
    ],
    source: Annotated[str, Field(description="Full new contents of checkout.py.")],
    summary: Annotated[str, Field(description="One-line description of the change.")],
    commit_sha: Annotated[str, Field(description="Commit SHA being deployed.")] = "",
) -> dict:
    """Deploy to production, if and only if a human approved this deployment.

    The approval token is *looked up*, never accepted as an argument. A model that
    invented a token would have nowhere to put it.
    """
    try:
        approval = approvals.consume(deployment_id)
    except HumanApprovalRequired as exc:
        return {
            "error": "human_approval_required",
            "deployment_id": deployment_id,
            "detail": str(exc),
            "production_changed": False,
        }

    result = _guard(
        lambda: simulator.post(
            "/_control/deploy",
            {
                "revision_id": "4c22",
                "summary": summary,
                "healthy": True,
                "source": source,
                "commit_sha": commit_sha,
                "deployed_by": "patchpilot",
            },
        )
    )
    if "error" in result:
        # The deployment failed after the approval was spent. Say so plainly: a
        # silent retry here would be a second unapproved production attempt.
        return {
            **result,
            "deployment_id": deployment_id,
            "production_changed": False,
            "detail": "Deployment failed. Production is unchanged and the approval "
            "has been spent; a new human approval is required to try again.",
        }
    return {
        "status": "deployed",
        "deployment_id": deployment_id,
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at,
        "production_changed": True,
        "deployment": result.get("deployment"),
        "health": result.get("health"),
    }


@server.tool(
    description="Current production health. Call repeatedly after a deployment to "
    "confirm the incident has actually recovered."
)
def get_production_health() -> dict:
    return _guard(lambda: simulator.get("/health"))


def main() -> None:
    import uvicorn

    port = int(os.environ.get("MCP_DEPLOYMENT_PORT", "8103"))
    uvicorn.run(server.streamable_http_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
