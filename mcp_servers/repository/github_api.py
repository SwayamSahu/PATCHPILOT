"""GitHub REST client for real pull requests and real review feedback.

Only the calls PatchPilot actually needs, so the credential's blast radius stays
small: open a pull request, read one back, and read the review comments on it.

The token is read from the environment at call time and never returned in any
tool result. Errors are redacted before they can be surfaced to the model.
"""

from __future__ import annotations

import os

import httpx

from mcp_servers.common.config import get

API_ROOT = "https://api.github.com"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class GitHubError(RuntimeError):
    """A GitHub API call failed."""


class GitHubNotConfigured(GitHubError):
    """Credentials or repository coordinates are missing."""


def _redact(text: str) -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    return text.replace(token, "***redacted***") if token and token in text else text


def repo_slug() -> str:
    owner, repo = get("GITHUB_OWNER"), get("GITHUB_REPO")
    if not owner or not repo:
        raise GitHubNotConfigured("GITHUB_OWNER and GITHUB_REPO must be set in .env")
    return f"{owner}/{repo}"


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise GitHubNotConfigured("GITHUB_TOKEN is not set; cannot reach GitHub")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _request(method: str, path: str, **kwargs) -> dict | list:
    url = f"{API_ROOT}{path}"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.request(method, url, headers=_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise GitHubError(f"could not reach GitHub: {_redact(str(exc))}") from exc
    if response.status_code >= 400:
        raise GitHubError(
            f"GitHub returned {response.status_code} for {path}: {_redact(response.text[:400])}"
        )
    return response.json()


def create_pull_request(title: str, body: str, head: str, base: str) -> dict:
    data = _request(
        "POST",
        f"/repos/{repo_slug()}/pulls",
        json={"title": title, "body": body, "head": head, "base": base},
    )
    return {
        "number": data["number"],
        "url": data["html_url"],
        "state": data["state"],
        "head": head,
        "base": base,
        "title": title,
    }


def _paginate(path: str, limit: int = 300) -> list:
    """Collect every page of a list endpoint.

    GitHub returns 30 items per page by default. A single request would silently
    drop review feedback beyond the first page, and the Developer agent would
    believe it had absorbed every finding while never having seen some of them -
    the worst possible way to lose a code review comment.
    """
    collected, page = [], 1
    while len(collected) < limit:
        batch = _request("GET", path, params={"per_page": 100, "page": page})
        if not isinstance(batch, list) or not batch:
            break
        collected.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return collected


def _login(actor) -> str:
    """A commenter's login, tolerating deleted accounts.

    GitHub leaves `user` null when the account is gone. Dereferencing it blindly
    turned one deleted reviewer into a crash that took the whole pull request
    read with it.
    """
    if not isinstance(actor, dict):
        return "ghost"
    return actor.get("login") or "ghost"


def get_pull_request(number: int) -> dict:
    slug = repo_slug()
    pr = _request("GET", f"/repos/{slug}/pulls/{number}")
    comments = _paginate(f"/repos/{slug}/issues/{number}/comments")
    inline = _paginate(f"/repos/{slug}/pulls/{number}/comments")
    reviews = _paginate(f"/repos/{slug}/pulls/{number}/reviews")
    return {
        "number": pr["number"],
        "url": pr["html_url"],
        "state": pr["state"],
        "merged": bool(pr.get("merged_at")),
        "mergeable": pr.get("mergeable"),
        "head": pr["head"]["ref"],
        "base": pr["base"]["ref"],
        "head_sha": pr["head"]["sha"],
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "changed_files": pr.get("changed_files"),
        "review_comments": [
            {
                "author": _login(c.get("user")),
                "body": c.get("body", ""),
                "path": c.get("path"),
                "line": c.get("line"),
                "kind": "inline",
            }
            for c in inline
        ]
        + [
            {"author": _login(c.get("user")), "body": c.get("body", ""), "kind": "comment"}
            for c in comments
        ],
        "reviews": [
            {
                "author": _login(r.get("user")),
                "state": r.get("state", ""),
                "body": r.get("body", "") or "",
            }
            for r in reviews
        ],
    }
