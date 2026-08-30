"""Git operations against a real working clone.

PatchPilot does not simulate version control. The Developer agent branches,
edits, commits, and pushes an actual git repository, and the pull request it
opens is a real pull request that a real reviewer reviews.

Two boundaries are enforced here rather than left to the agent's judgement:

* **Every path is jailed to the work repo.** Absolute paths, `..` traversal, and
  symlinks that escape are rejected before any write happens.
* **Some paths are refused outright** even inside the repo — `.git/` (rewriting
  history or hooks), `.github/workflows/` (an agent that can edit CI can execute
  anything on push), and `.env` (secrets). None of these are needed to fix a
  checkout bug, so allowing them buys nothing and risks a great deal.

The agent works in a dedicated clone, never in the developer's checkout, so a
confused agent cannot disturb work in progress.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mcp_servers.common.config import REPO_ROOT, get

GIT_TIMEOUT = 60
TEST_TIMEOUT = 300

DENIED_PREFIXES = (".git/", ".github/workflows/")
DENIED_NAMES = (".env",)


class RepositoryError(RuntimeError):
    """A git operation failed, or was refused."""


class PathNotAllowed(RepositoryError):
    """The requested path is outside the repo or on the refused list."""


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def work_repo() -> Path:
    """Where the agent's clone lives."""
    return Path(get("WORK_REPO_PATH", str(REPO_ROOT / ".workrepo"))).resolve()


def base_branch() -> str:
    return get("PATCHPILOT_BASE_BRANCH", get("GITHUB_DEFAULT_BRANCH", "main"))


def _redact(text: str) -> str:
    """Strip anything token-shaped before it can reach a log or the model."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token and token in text:
        text = text.replace(token, "***redacted***")
    return text


def run_git(*args: str, cwd: Path | None = None, timeout: int = GIT_TIMEOUT) -> CommandResult:
    """Run a git command in the work repo with credentials supplied out of band."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang waiting for a password
    askpass = _askpass_script()
    if askpass:
        env["GIT_ASKPASS"] = str(askpass)
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd or work_repo()),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError(f"git {' '.join(args)} timed out after {timeout}s") from exc
    except FileNotFoundError as exc:  # pragma: no cover - git is a hard requirement
        raise RepositoryError("git is not installed") from exc
    return CommandResult(
        completed.returncode, _redact(completed.stdout), _redact(completed.stderr)
    )


def _askpass_script() -> Path | None:
    """Write a helper that feeds git the token from the environment.

    Passing credentials this way keeps the token out of the remote URL, out of
    `.git/config`, and out of any command line that might be logged.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return None
    path = work_repo().parent / ".patchpilot-askpass.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Always rewrite it. Reusing whatever is already at this path would execute
    # someone else's script with the GitHub token in its environment, which is a
    # credential handover rather than a convenience.
    if path.is_symlink():
        path.unlink()
    path.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  Username*) printf '%s' \"x-access-token\" ;;\n"
        "  *)         printf '%s' \"$GITHUB_TOKEN\" ;;\n"
        "esac\n"
    )
    path.chmod(0o700)
    return path


def git_or_raise(*args: str, **kwargs) -> str:
    result = run_git(*args, **kwargs)
    if not result.ok:
        raise RepositoryError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def resolve_path(relative: str) -> Path:
    """Resolve a repo-relative path, refusing anything outside or off-limits.

    Checked before any read or write. The check uses the fully resolved path, so
    a symlink pointing out of the repo is caught rather than followed.
    """
    if not relative or relative.strip() in {".", "/"}:
        raise PathNotAllowed("a file path is required")

    candidate = Path(relative)
    if candidate.is_absolute():
        raise PathNotAllowed(f"absolute paths are not allowed: {relative!r}")

    root = work_repo()
    resolved = (root / candidate).resolve()
    try:
        inside = resolved.relative_to(root)
    except ValueError:
        raise PathNotAllowed(
            f"{relative!r} resolves outside the repository and was refused"
        ) from None

    posix = inside.as_posix()
    if any(posix.startswith(prefix) for prefix in DENIED_PREFIXES) or posix in DENIED_NAMES:
        raise PathNotAllowed(
            f"{posix!r} is off limits. Repository internals, CI workflows, and "
            "environment files cannot be modified by an agent."
        )
    return resolved


# --------------------------------------------------------------------------
# Repository lifecycle
# --------------------------------------------------------------------------


def is_ready() -> bool:
    return (work_repo() / ".git").exists()


def clone(remote_url: str, branch: str | None = None) -> Path:
    """Create the agent's working clone, replacing any previous one."""
    target = work_repo()
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--quiet"]
    if branch:
        args += ["--branch", branch]
    args += [remote_url, str(target)]
    result = run_git(*args, cwd=target.parent, timeout=180)
    if not result.ok:
        raise RepositoryError(f"clone failed: {result.stderr.strip()}")
    run_git("config", "user.email", get("GIT_AUTHOR_EMAIL", "patchpilot@users.noreply.github.com"))
    run_git("config", "user.name", get("GIT_AUTHOR_NAME", "PatchPilot"))
    return target


def require_ready() -> None:
    if not is_ready():
        raise RepositoryError(
            f"no working clone at {work_repo()}. Run scripts/reset-demo.sh to prepare it."
        )


# --------------------------------------------------------------------------
# Read operations
# --------------------------------------------------------------------------


def read_file(relative: str) -> dict:
    require_ready()
    path = resolve_path(relative)
    if not path.is_file():
        raise RepositoryError(f"{relative!r} does not exist in the repository")
    content = path.read_text()
    return {
        "path": relative,
        "content": content,
        "lines": content.count("\n") + 1,
    }


def history(relative: str | None = None, limit: int = 10) -> list:
    require_ready()
    args = ["log", f"-{limit}", "--pretty=format:%H%x1f%an%x1f%aI%x1f%s"]
    if relative:
        resolve_path(relative)
        args += ["--", relative]
    output = git_or_raise(*args)
    commits = []
    for line in output.splitlines():
        if not line.strip():
            continue
        sha, author, date, subject = line.split("\x1f")
        commits.append(
            {"sha": sha, "short_sha": sha[:7], "author": author, "date": date, "subject": subject}
        )
    return commits


def commit_details(sha: str) -> dict:
    require_ready()
    output = git_or_raise("show", "--no-patch", "--pretty=format:%H%x1f%an%x1f%aI%x1f%s%x1f%b", sha)
    parts = output.split("\x1f")
    files = git_or_raise("show", "--name-only", "--pretty=format:", sha).split()
    return {
        "sha": parts[0],
        "short_sha": parts[0][:7],
        "author": parts[1],
        "date": parts[2],
        "subject": parts[3],
        "body": parts[4].strip() if len(parts) > 4 else "",
        "files_changed": files,
    }


def diff(sha: str) -> dict:
    require_ready()
    return {"sha": sha, "diff": git_or_raise("show", "--format=", sha)}


def read_file_at(commit_sha: str, relative: str) -> str:
    """Read a file's contents as of a specific commit.

    Used to fetch the exact artifact a deployment was prepared from. Reading from
    an immutable commit rather than the working tree means the thing deployed is
    the thing reviewed, even if the checkout has moved on since.
    """
    require_ready()
    resolve_path(relative)
    result = run_git("show", f"{commit_sha}:{relative}")
    if not result.ok:
        raise RepositoryError(
            f"could not read {relative!r} at commit {commit_sha!r}: {result.stderr.strip()}"
        )
    return result.stdout


def working_diff() -> dict:
    """The uncommitted change set — what the agent has written but not committed."""
    require_ready()
    return {"diff": git_or_raise("diff"), "staged": git_or_raise("diff", "--cached")}


def branch_diff(base: str | None = None) -> str:
    """Everything this branch changes relative to its base, committed or not.

    The whole change set, which is what "did the agent make the fix?" actually
    means. Looking only at uncommitted changes reports an empty diff the moment
    the agent commits - and committing is the normal, correct thing for it to do,
    so that reading fails a stage that in fact succeeded.
    """
    require_ready()
    base = base or base_branch()
    committed = run_git("diff", f"{base}...HEAD")
    uncommitted = run_git("diff", base)
    return (committed.stdout if committed.ok else "") or (
        uncommitted.stdout if uncommitted.ok else ""
    )


def current_branch() -> str:
    require_ready()
    return git_or_raise("rev-parse", "--abbrev-ref", "HEAD").strip()


def head_sha() -> str:
    require_ready()
    return git_or_raise("rev-parse", "HEAD").strip()


# --------------------------------------------------------------------------
# Write operations
# --------------------------------------------------------------------------


def _is_plain_branch_name(name: str) -> bool:
    """True for an ordinary branch name: no options, refspecs, or path tricks."""
    if not name or name.startswith("-") or name.startswith("/"):
        return False
    forbidden = {":", " ", "\t", "~", "^", "?", "*", "[", "\\"}
    return not (set(name) & forbidden) and ".." not in name and not name.endswith(".lock")


def create_branch(name: str, base: str | None = None) -> dict:
    """Create a branch from `base`, resetting it if it already exists.

    A retry must start from the base it reports, not from whatever a previous
    attempt left behind. Checking out a stale branch and returning the requested
    base would make a second attempt inherit the first one's half-finished work
    while claiming a clean start.
    """
    require_ready()
    if not _is_plain_branch_name(name):
        raise RepositoryError(f"invalid branch name {name!r}")
    base = base or base_branch()
    git_or_raise("checkout", base)
    result = run_git("checkout", "-b", name)
    if not result.ok:
        if "already exists" in result.stderr:
            git_or_raise("checkout", "-B", name, base)
        else:
            raise RepositoryError(f"could not create branch {name!r}: {result.stderr.strip()}")
    return {"branch": name, "base": base, "head": head_sha()}


def write_file(relative: str, content: str) -> dict:
    """Write a file inside the repo, after the path jail has cleared it."""
    require_ready()
    path = resolve_path(relative)
    existed = path.is_file()
    before = path.read_text() if existed else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return {
        "path": relative,
        "created": not existed,
        "bytes_written": len(content.encode()),
        "changed": before != content,
    }


def edit_file(relative: str, old_string: str, new_string: str) -> dict:
    """Replace one exact occurrence of `old_string` with `new_string`.

    Preferred over rewriting a whole file. Two reasons, and both matter:

    * It makes an over-broad change hard to perform by accident. The edit is
      confined to text the caller had to quote exactly, so a minimal fix stays
      minimal and the diff stays reviewable.
    * It is far more reliable for a smaller model, which can restate one line
      accurately but tends to drop or mangle content when asked to reproduce an
      entire file.

    The match must be unique. An ambiguous edit is refused rather than applied to
    the first hit, because "the first one" is rarely the one that was meant.
    """
    require_ready()
    path = resolve_path(relative)
    if not path.is_file():
        raise RepositoryError(f"{relative!r} does not exist in the repository")
    if not old_string:
        raise RepositoryError("old_string must not be empty")

    content = path.read_text()
    occurrences = content.count(old_string)
    if occurrences == 0:
        raise RepositoryError(
            f"the text to replace was not found in {relative!r}. Read the file again "
            "and quote the exact text, including indentation."
        )
    if occurrences > 1:
        raise RepositoryError(
            f"the text to replace appears {occurrences} times in {relative!r}. "
            "Include surrounding lines to make it unique."
        )

    path.write_text(content.replace(old_string, new_string))
    return {
        "path": relative,
        "replaced": True,
        "lines_before": content.count("\n") + 1,
        "lines_after": content.replace(old_string, new_string).count("\n") + 1,
    }


def append_to_file(relative: str, content: str) -> dict:
    """Append text to a file, creating it if absent.

    Adding a regression test is an append, not a rewrite. Expressing it that way
    means an agent adding a test cannot accidentally delete the tests already
    there.
    """
    require_ready()
    path = resolve_path(relative)
    existed = path.is_file()
    before = path.read_text() if existed else ""
    if not before or before.endswith("\n\n"):
        separator = ""
    elif before.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(before + separator + content.rstrip("\n") + "\n")
    return {"path": relative, "created": not existed, "appended_bytes": len(content.encode())}


def commit(message: str) -> dict:
    require_ready()
    if not message.strip():
        raise RepositoryError("a commit message is required")
    # -uall lists untracked files individually. Without it git collapses a new
    # directory to ".github/", and a prefix check for ".github/workflows/" would
    # sail straight past the file it was meant to catch.
    status = git_or_raise("status", "--porcelain", "-uall")
    if not status.strip():
        raise RepositoryError("there is nothing to commit")

    # `git add -A` would stage anything in the tree, including files the write
    # tools refuse. A hostile or careless test run could drop a workflow file into
    # the working tree, and committing it would smuggle it past the path rules on
    # the way to a pull request. Stage explicitly, and refuse if a denied path has
    # appeared at all rather than quietly leaving it behind.
    changed = [line[3:].strip().strip('"') for line in status.splitlines() if line.strip()]
    changed = [path.split(" -> ")[-1] for path in changed]
    denied = [
        path
        for path in changed
        if any(path.startswith(prefix) for prefix in DENIED_PREFIXES) or path in DENIED_NAMES
    ]
    if denied:
        raise RepositoryError(
            f"refusing to commit: off-limits paths changed in the working tree: {denied}"
        )
    for path in changed:
        git_or_raise("add", "--", path)
    result = run_git("commit", "-m", message)
    if not result.ok:
        raise RepositoryError(f"commit failed: {result.stderr.strip()}")
    return {"sha": head_sha(), "branch": current_branch(), "message": message}


def push(branch: str | None = None) -> dict:
    require_ready()
    branch = branch or current_branch()

    # A bare branch argument is passed to git as a refspec, so "HEAD:main" would
    # push the working tree straight onto the base branch, and "--all" would push
    # everything - both bypassing the pull request the review depends on. Only a
    # plain branch name is accepted, and it is expanded to an explicit refspec.
    if not _is_plain_branch_name(branch):
        raise RepositoryError(
            f"{branch!r} is not a plain branch name. Refspecs and options are not accepted."
        )
    refspec = f"refs/heads/{branch}:refs/heads/{branch}"
    result = run_git("push", "--set-upstream", "origin", refspec, timeout=180)
    if not result.ok:
        raise RepositoryError(f"push failed: {result.stderr.strip()}")
    return {"branch": branch, "pushed": True}


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


SECRET_ENV_PREFIXES = ("GITHUB_", "DAYTONA_", "OPENAI_", "ANTHROPIC_", "PATCHPILOT_INTERNAL")
SECRET_ENV_NAMES = {"GITHUB_TOKEN", "GH_PUSH_TOKEN", "GH_TOKEN"}


def _test_env() -> dict:
    """A minimal environment for running agent-authored tests.

    Running a test suite *is* arbitrary code execution: pytest imports and runs
    whatever the agent wrote. Inheriting the parent environment would hand that
    code the GitHub token, and redacting output afterwards is no defence at all -
    code can encode a secret, write it to a file, or send it over the network.

    So the subprocess gets only what it needs to run Python, and every
    credential-shaped variable is withheld. The blast radius of a hostile test is
    then the throwaway clone it runs in.
    """
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(work_repo())
    leaked = [
        name
        for name in env
        if name in SECRET_ENV_NAMES or name.startswith(SECRET_ENV_PREFIXES)
    ]
    for name in leaked:  # pragma: no cover - belt and braces
        env.pop(name, None)
    return env


def run_tests(target: str = "tests/", timeout: int = TEST_TIMEOUT) -> dict:
    """Run the repository's test suite and report the real result.

    Whatever the agent claims about its fix, this is what decides. The command is
    time limited so a hanging test cannot stall the workflow indefinitely.
    """
    require_ready()

    # `--basetemp=/somewhere` is not a path, it is a pytest option, and pytest
    # clears the directory it names. Rejecting leading dashes and passing the
    # target after `--` keeps a test selector from becoming an option.
    if target.startswith("-"):
        raise PathNotAllowed(f"{target!r} is an option, not a test path")
    resolve_path(target.rstrip("/") or ".")

    python = work_repo() / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path(REPO_ROOT) / ".venv" / "bin" / "python"
    try:
        completed = subprocess.run(
            [str(python), "-m", "pytest", "-q", "--", target],
            cwd=str(work_repo()),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_test_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "timed_out": True,
            "exit_code": None,
            "summary": f"tests exceeded the {timeout}s limit and were stopped",
            "stdout": "",
            "stderr": "",
        }
    stdout = _redact(completed.stdout)
    return {
        "passed": completed.returncode == 0,
        "timed_out": False,
        "exit_code": completed.returncode,
        "summary": stdout.strip().splitlines()[-1] if stdout.strip() else "",
        "stdout": stdout[-4000:],
        "stderr": _redact(completed.stderr)[-2000:],
    }
