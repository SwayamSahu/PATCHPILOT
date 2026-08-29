"""Tool annotations are the permission model, so they are tested like one.

TrueForge scopes an agent's tools with selectors such as `@read-only` and
`@write`, and those selectors are resolved from each tool's MCP annotations.
Without annotations every tool is unclassified, and `enable_tools: ["@read-only"]`
grants **zero** tools rather than the read-only ones — an agent configured that
way silently receives nothing at all and cannot do its job.

The failure is quiet in both directions, which is why it is asserted here:

* a read tool that loses its annotation disappears from a read-only agent, and
* a write tool that gains one becomes visible to an agent meant to be read-only.

The second is a security regression, not an inconvenience.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_servers.deployment import server as deployment_server
from mcp_servers.repository import server as repository_server
from mcp_servers.telemetry import server as telemetry_server

EXPECTED_READ_ONLY = {
    "telemetry": {
        "get_service_health", "get_metrics", "query_logs",
        "get_recent_deployments", "get_incident_details", "get_error_samples",
    },
    "repository": {
        "get_repository_file", "get_git_history", "get_commit", "get_diff",
        "get_working_diff", "get_pull_request", "run_git_tests",
    },
    "deployment": {
        "get_staging_health", "get_production_health", "prepare_production_deployment",
    },
}

SERVERS = {
    "telemetry": telemetry_server.server,
    "repository": repository_server.server,
    "deployment": deployment_server.server,
}


def tools(name):
    return asyncio.run(SERVERS[name].list_tools())


@pytest.mark.parametrize("server_name", sorted(SERVERS))
def test_every_tool_declares_its_annotations(server_name):
    """An unannotated tool is invisible to every selector-scoped agent."""
    missing = [t.name for t in tools(server_name) if t.annotations is None]
    assert not missing, f"tools without annotations would be unscopable: {missing}"


@pytest.mark.parametrize("server_name", sorted(SERVERS))
def test_read_only_classification_is_exactly_as_intended(server_name):
    read_only = {t.name for t in tools(server_name) if t.annotations.read_only_hint}
    assert read_only == EXPECTED_READ_ONLY[server_name]


def test_no_repository_write_tool_is_marked_read_only():
    """A mislabelled write tool would leak into a read-only agent's tool set."""
    for tool in tools("repository"):
        if tool.name in {"write_file", "create_branch", "commit_changes", "push_branch"}:
            assert tool.annotations.read_only_hint is False


def test_production_deployment_is_marked_destructive():
    """`require_approval_for_tools: ["@destructive"]` must catch this tool."""
    deploy = next(t for t in tools("deployment") if t.name == "deploy_production")
    assert deploy.annotations.destructive_hint is True
    assert deploy.annotations.read_only_hint is False


def test_staging_deployment_is_a_write_but_not_destructive():
    """Staging is reversible, so it is deliberately not behind the approval gate."""
    staging = next(t for t in tools("deployment") if t.name == "deploy_staging")
    assert staging.annotations.read_only_hint is False
    assert staging.annotations.destructive_hint is False
