"""Repository MCP server — real git, real GitHub.

The Developer agent uses these tools to branch, patch, test, commit, and open a
pull request. Nothing here is simulated: the branch exists, the commit exists, the
pull request is reviewed by the same reviewer that reviews human work.

Read tools and write tools live in one server but are separable by name, so an
agent can be given `@read-only` and genuinely lose the ability to modify anything.
"""

from __future__ import annotations

import os
import sys
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mcp_servers.repository import github_api, gitrepo  # noqa: E402
from mcp_servers.repository.github_api import GitHubError  # noqa: E402
from mcp_servers.repository.gitrepo import RepositoryError  # noqa: E402

server = MCPServer(
    name="patchpilot-repository",
    instructions=(
        "Git and GitHub tools for the service under investigation. Read the code "
        "and its history to understand a defect; create a branch, write a minimal "
        "fix and a regression test, run the tests, and open a pull request. "
        "Keep changes minimal and confined to the files responsible for the "
        "defect. Repository internals, CI workflows, and environment files cannot "
        "be modified and attempting to do so will be refused."
    ),
)


def _guard(call):
    """Report failures as structured results the agent can reason about."""
    try:
        return call()
    except RepositoryError as exc:
        return {"error": "repository_error", "detail": str(exc)}
    except GitHubError as exc:
        return {"error": "github_error", "detail": str(exc)}


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Read a file from the repository by its repo-relative path.")
def get_repository_file(
    path: Annotated[str, Field(description="Repo-relative path, e.g. 'app/checkout.py'.")],
) -> dict:
    return _guard(lambda: gitrepo.read_file(path))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Commit history, most recent first. Pass a path to see only the "
    "commits that touched that file - useful for finding when a defect appeared."
)
def get_git_history(
    path: Annotated[str, Field(description="Optional repo-relative path to filter by.")] = "",
    limit: Annotated[int, Field(description="How many commits to return.", ge=1, le=50)] = 10,
) -> dict:
    return _guard(lambda: {"commits": gitrepo.history(path or None, limit)})


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Details of a single commit: author, date, message, and files changed.")
def get_commit(
    commit_sha: Annotated[str, Field(description="Full or short commit SHA.")],
) -> dict:
    return _guard(lambda: gitrepo.commit_details(commit_sha))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="The full diff introduced by a commit.")
def get_diff(
    commit_sha: Annotated[str, Field(description="Full or short commit SHA.")],
) -> dict:
    return _guard(lambda: gitrepo.diff(commit_sha))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="The uncommitted changes currently in the working tree - what has "
    "been written but not yet committed."
)
def get_working_diff() -> dict:
    return _guard(gitrepo.working_diff)


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------


@server.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    description="Create a branch from the base branch and switch to it.")
def create_branch(
    branch_name: Annotated[
        str, Field(description="New branch name, e.g. 'fix/checkout-discount'.")
    ],
    base: Annotated[str, Field(description="Branch to start from. Defaults to main.")] = "",
) -> dict:
    return _guard(lambda: gitrepo.create_branch(branch_name, base or None))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    description="Write a file in the repository, replacing its contents. Paths "
    "outside the repository, repository internals, CI workflows, and environment "
    "files are refused."
)
def write_file(
    path: Annotated[str, Field(description="Repo-relative path to write.")],
    content: Annotated[str, Field(description="Full new file contents.")],
) -> dict:
    return _guard(lambda: gitrepo.write_file(path, content))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    description="Replace one exact, unique piece of text in a file. Prefer this "
    "over write_file: it keeps the change minimal and you only have to restate "
    "the lines you are changing. The old text must appear exactly once.",
)
def edit_file(
    path: Annotated[str, Field(description="Repo-relative path to edit.")],
    old_string: Annotated[str, Field(description="Exact text to replace, including indentation.")],
    new_string: Annotated[str, Field(description="Replacement text.")],
) -> dict:
    return _guard(lambda: gitrepo.edit_file(path, old_string, new_string))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    description="Append text to the end of a file, creating it if needed. Use this "
    "to add a regression test without rewriting the tests already there.",
)
def append_to_file(
    path: Annotated[str, Field(description="Repo-relative path.")],
    content: Annotated[str, Field(description="Text to append.")],
) -> dict:
    return _guard(lambda: gitrepo.append_to_file(path, content))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Run the repository's test suite and return the real result. This "
    "is what decides whether a fix works."
)
def run_git_tests(
    target: Annotated[str, Field(description="Test path to run, e.g. 'tests/'.")] = "tests/",
) -> dict:
    return _guard(lambda: gitrepo.run_tests(target))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    description="Commit all current changes to the active branch.")
def commit_changes(
    message: Annotated[str, Field(description="Commit message.")],
) -> dict:
    return _guard(lambda: gitrepo.commit(message))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    description="Push the current branch to GitHub.")
def push_branch(
    branch: Annotated[str, Field(description="Branch to push. Defaults to the active one.")] = "",
) -> dict:
    return _guard(lambda: gitrepo.push(branch or None))


@server.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    description="Open a pull request on GitHub for a pushed branch. Returns the "
    "PR number and URL."
)
def create_pull_request(
    title: Annotated[str, Field(description="Pull request title.")],
    body: Annotated[str, Field(description="Pull request description.")],
    branch: Annotated[str, Field(description="Head branch carrying the change.")],
    base: Annotated[str, Field(description="Branch to merge into. Defaults to main.")] = "",
) -> dict:
    return _guard(
        lambda: github_api.create_pull_request(
            title=title, body=body, head=branch, base=base or gitrepo.base_branch()
        )
    )


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    description="Read a pull request including its review comments. Use this to "
    "collect code review findings and address them."
)
def get_pull_request(
    pr_number: Annotated[int, Field(description="Pull request number.")],
) -> dict:
    return _guard(lambda: github_api.get_pull_request(pr_number))


def main() -> None:
    import uvicorn

    port = int(os.environ.get("MCP_REPOSITORY_PORT", "8102"))
    uvicorn.run(server.streamable_http_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
