"""Tests for the repository MCP server.

The git operations are exercised against a real repository rather than mocks: a
mocked git would let a broken path jail pass, and the path jail is the boundary
that stops a confused or hijacked agent from writing outside the project.
"""

from __future__ import annotations

import subprocess

import pytest

from mcp_servers.repository import gitrepo
from mcp_servers.repository import server as repo_server
from mcp_servers.repository.gitrepo import PathNotAllowed


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A real git repository standing in for the service under investigation."""
    root = tmp_path / "workrepo"
    root.mkdir()
    monkeypatch.setenv("WORK_REPO_PATH", str(root))
    monkeypatch.setenv("PATCHPILOT_BASE_BRANCH", "main")

    def git(*args, **kwargs):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, **kwargs)

    git("init", "-q", "--initial-branch=main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")

    (root / "app").mkdir()
    (root / "app" / "checkout.py").write_text(
        "def compute_total(subtotal, discount):\n    return subtotal * (1 - discount)\n"
    )
    git("add", "-A")
    git("commit", "-qm", "checkout: initial pricing")

    (root / "app" / "checkout.py").write_text(
        "def compute_total(subtotal, discount):\n    return subtotal / discount\n"
    )
    git("add", "-A")
    git("commit", "-qm", "checkout: simplify discount calculation")

    (root / "secret.txt").write_text("not a secret, just a canary\n")
    (root / ".env").write_text("GITHUB_TOKEN=should-never-be-writable\n")
    git("add", "-A")
    git("commit", "-qm", "add fixtures")
    return root


# --------------------------------------------------------------------------
# The path jail
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "../../etc/passwd",
        "../outside.py",
        "/etc/passwd",
        "app/../../escape.py",
        ".git/config",
        ".git/hooks/pre-commit",
        ".github/workflows/ci.yml",
        ".env",
    ],
)
def test_paths_outside_or_off_limits_are_refused(repo, bad_path):
    with pytest.raises(PathNotAllowed):
        gitrepo.resolve_path(bad_path)


def test_write_file_refuses_to_escape_the_repository(repo, tmp_path):
    victim = tmp_path / "outside.py"
    result = repo_server.write_file(path="../outside.py", content="owned")
    assert result["error"] == "repository_error"
    assert not victim.exists(), "a refused write must not touch the filesystem"


def test_write_file_refuses_to_modify_ci_workflows(repo):
    """An agent that can edit CI can execute arbitrary code on push."""
    result = repo_server.write_file(path=".github/workflows/ci.yml", content="run: curl evil")
    assert result["error"] == "repository_error"
    assert not (repo / ".github" / "workflows" / "ci.yml").exists()


def test_write_file_refuses_to_touch_env_files(repo):
    before = (repo / ".env").read_text()
    result = repo_server.write_file(path=".env", content="GITHUB_TOKEN=stolen")
    assert result["error"] == "repository_error"
    assert (repo / ".env").read_text() == before


def test_symlink_escaping_the_repo_is_refused(repo, tmp_path):
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (repo / "link").symlink_to(outside)
    with pytest.raises(PathNotAllowed):
        gitrepo.resolve_path("link/escaped.py")


def test_allowed_paths_still_resolve(repo):
    assert gitrepo.resolve_path("app/checkout.py").is_file()


# --------------------------------------------------------------------------
# Reading the code and its history
# --------------------------------------------------------------------------


def test_read_a_file(repo):
    result = repo_server.get_repository_file(path="app/checkout.py")
    assert "subtotal / discount" in result["content"]


def test_reading_a_missing_file_reports_an_error(repo):
    assert repo_server.get_repository_file(path="app/nope.py")["error"] == "repository_error"


def test_history_for_a_path_finds_the_offending_commit(repo):
    commits = repo_server.get_git_history(path="app/checkout.py", limit=10)["commits"]
    assert [c["subject"] for c in commits][0] == "checkout: simplify discount calculation"
    assert len(commits) == 2


def test_commit_details_and_diff_expose_the_change(repo):
    sha = repo_server.get_git_history(path="app/checkout.py")["commits"][0]["sha"]
    details = repo_server.get_commit(commit_sha=sha)
    assert details["files_changed"] == ["app/checkout.py"]
    diff = repo_server.get_diff(commit_sha=sha)["diff"]
    assert "-    return subtotal * (1 - discount)" in diff
    assert "+    return subtotal / discount" in diff


# --------------------------------------------------------------------------
# Branching, patching, committing
# --------------------------------------------------------------------------


def test_branch_write_and_commit_round_trip(repo):
    branch = repo_server.create_branch(branch_name="fix/checkout-discount")
    assert branch["branch"] == "fix/checkout-discount"

    written = repo_server.write_file(
        path="app/checkout.py",
        content="def compute_total(subtotal, discount):\n    return subtotal * (1 - discount)\n",
    )
    assert written["changed"] is True

    diff = repo_server.get_working_diff()["diff"]
    assert "subtotal * (1 - discount)" in diff

    result = repo_server.commit_changes(message="fix(checkout): restore discount multiplication")
    assert result["branch"] == "fix/checkout-discount"
    assert len(result["sha"]) == 40


def test_committing_nothing_is_an_error_not_a_silent_success(repo):
    repo_server.create_branch(branch_name="fix/empty")
    assert repo_server.commit_changes(message="no changes")["error"] == "repository_error"


def test_invalid_branch_names_are_refused(repo):
    for name in ("--upload-pack=evil", "has space", "a..b"):
        assert repo_server.create_branch(branch_name=name)["error"] == "repository_error"


# --------------------------------------------------------------------------
# Targeted edits
# --------------------------------------------------------------------------


def test_edit_file_replaces_a_unique_string(repo):
    result = repo_server.edit_file(
        path="app/checkout.py",
        old_string="return subtotal / discount",
        new_string="return subtotal * (1 - discount)",
    )
    assert result["replaced"] is True
    assert "subtotal * (1 - discount)" in (repo / "app" / "checkout.py").read_text()


def test_edit_file_refuses_an_ambiguous_match(repo):
    """Applying to the first hit is rarely what was meant, so refuse instead."""
    (repo / "app" / "dup.py").write_text("x = 1\nx = 1\n")
    result = repo_server.edit_file(path="app/dup.py", old_string="x = 1", new_string="x = 2")
    assert result["error"] == "repository_error"
    assert "appears 2 times" in result["detail"]
    assert (repo / "app" / "dup.py").read_text() == "x = 1\nx = 1\n"


def test_edit_file_reports_a_missing_match_rather_than_guessing(repo):
    result = repo_server.edit_file(
        path="app/checkout.py", old_string="not in the file", new_string="x"
    )
    assert result["error"] == "repository_error"
    assert "not found" in result["detail"]


def test_edit_file_is_subject_to_the_path_jail(repo):
    assert repo_server.edit_file(path="../escape.py", old_string="a", new_string="b")["error"]
    assert repo_server.edit_file(path=".env", old_string="GITHUB_TOKEN", new_string="x")["error"]


def test_append_to_file_preserves_existing_content(repo):
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_one():\n    assert True\n")
    repo_server.append_to_file(
        path="tests/test_x.py", content="def test_two():\n    assert True\n"
    )
    body = (repo / "tests" / "test_x.py").read_text()
    assert "def test_one" in body and "def test_two" in body


# --------------------------------------------------------------------------
# Tests are really executed
# --------------------------------------------------------------------------


def test_run_git_tests_reports_a_genuine_failure(repo):
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_fails():\n    assert 1 == 2\n")
    result = repo_server.run_git_tests(target="tests/")
    assert result["passed"] is False
    assert result["exit_code"] != 0


def test_run_git_tests_reports_a_genuine_pass(repo):
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_passes():\n    assert 1 == 1\n")
    result = repo_server.run_git_tests(target="tests/")
    assert result["passed"] is True
    assert result["exit_code"] == 0


# --------------------------------------------------------------------------
# Secrets never leak
# --------------------------------------------------------------------------


def test_token_is_redacted_from_command_output(repo, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_supersecrettokenvalue")
    assert "ghp_supersecrettokenvalue" not in gitrepo._redact(
        "fatal: could not read from https://ghp_supersecrettokenvalue@github.com"
    )
    assert "***redacted***" in gitrepo._redact("token ghp_supersecrettokenvalue here")


def test_no_tool_returns_the_token(repo):
    """A tool result travels straight into model context; it must never carry a key."""
    results = [
        repo_server.get_repository_file(path="app/checkout.py"),
        repo_server.get_git_history(limit=5),
        repo_server.get_working_diff(),
    ]
    blob = repr(results)
    assert "GITHUB_TOKEN=" not in blob
    assert "ghp_" not in blob
