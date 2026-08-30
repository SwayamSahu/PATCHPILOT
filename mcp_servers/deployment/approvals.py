"""The production approval store.

This is the second of PatchPilot's two independent locks on production.

The first lock is the harness: TrueForge's `require_approval_for_tools` suspends
the agent loop before `deploy_production` runs, so the model is not executing when
the gate is reached and cannot reason its way past it.

This module is the second. A deployment is only executable if a human has minted
an approval for that specific deployment id. The resulting token is:

* **created only by the application**, in response to a human action — never by a
  tool the model can call, and never derived from anything in the transcript;
* **never placed in model context**, so it cannot be guessed, quoted, or replayed
  by a prompt injection that has captured the agent;
* **bound to one deployment id**, so an approval for a reviewed change cannot be
  redirected at a different one;
* **bound to the exact artifact**, by a digest of the source recorded when the
  deployment was prepared. Approving deployment X and then shipping different
  code under X's id would defeat the entire premise that a human reviewed what
  ships, so the digest is checked before the approval is even spent;
* **single use**, so a replayed call after a rollback does not redeploy;
* **time limited**, so an approval left open overnight is not still live.

The two locks are deliberately redundant. Either alone would satisfy the brief;
together they mean that even a fully compromised agent cannot ship to production.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_TTL_SECONDS = 900  # 15 minutes

STORE_PATH = Path(
    os.environ.get(
        "APPROVAL_STORE_PATH",
        Path(__file__).resolve().parents[2] / "simulator" / ".state" / "approvals.json",
    )
)

_lock = threading.RLock()


class ApprovalError(RuntimeError):
    """Base class for approval failures."""


class HumanApprovalRequired(ApprovalError):
    """Raised when production deployment is attempted without a human approval.

    This is the exception the whole safety model exists to raise.
    """


@dataclass
class Approval:
    deployment_id: str
    token: str
    approved_by: str
    approved_at: str
    expires_at: str
    consumed_at: str | None = None

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) > datetime.fromisoformat(self.expires_at)

    @property
    def consumed(self) -> bool:
        return self.consumed_at is not None


# --------------------------------------------------------------------------
# Prepared deployments
# --------------------------------------------------------------------------
#
# A prepared deployment records what a human is being asked to approve. The
# digest is what makes the approval mean something: without it, an approval
# authorises an id rather than a change, and any source at all could be shipped
# under an approved id.


def _pending_path() -> Path:
    return STORE_PATH.with_name("pending_deployments.json")


def source_digest(source: str) -> str:
    """Content address of the artifact under review."""
    return hashlib.sha256(source.encode()).hexdigest()


def prepare(
    deployment_id: str,
    source: str,
    summary: str,
    pr_number: int,
    commit_sha: str,
    artifact_path: str = "",
) -> dict:
    """Record what is being proposed, so approval can be tied to it."""
    with _lock:
        path = _pending_path()
        data = json.loads(path.read_text()) if path.exists() else {}
        record = {
            "deployment_id": deployment_id,
            "source_sha256": source_digest(source),
            "summary": summary,
            "pr_number": pr_number,
            "commit_sha": commit_sha,
            "path": artifact_path,
            "prepared_at": _now().isoformat(),
        }
        data[deployment_id] = record
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)
        return record


def get_prepared(deployment_id: str):
    path = _pending_path()
    if not path.exists():
        return None
    return json.loads(path.read_text()).get(deployment_id)


class ArtifactMismatch(ApprovalError):
    """The source offered for deployment is not the source that was approved."""


def verify_artifact(deployment_id: str, source: str) -> dict:
    """Confirm the source about to ship is the source that was reviewed.

    Checked *before* the approval is consumed, so a mismatched attempt does not
    burn a valid human approval.
    """
    record = get_prepared(deployment_id)
    if record is None:
        raise HumanApprovalRequired(
            f"deployment {deployment_id!r} was never prepared, so nothing was reviewed "
            "and there is nothing to approve."
        )
    actual = source_digest(source)
    if actual != record["source_sha256"]:
        raise ArtifactMismatch(
            f"the source offered for deployment {deployment_id!r} does not match what was "
            f"approved (expected sha256 {record['source_sha256'][:12]}…, got {actual[:12]}…). "
            "Production deployment is refused."
        )
    return record


def _now() -> datetime:
    return datetime.now(UTC)


def _read() -> dict:
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text())
    except json.JSONDecodeError:  # pragma: no cover - corrupt store is not fatal
        return {}


def _write(data: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STORE_PATH)


def grant(deployment_id: str, approved_by: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Approval:
    """Record a human's approval of one specific deployment.

    Called by the PatchPilot API when a person clicks APPROVE. It is deliberately
    not reachable as an MCP tool: nothing the model can invoke reaches this
    function.
    """
    if not deployment_id:
        raise ApprovalError("deployment_id is required")
    with _lock:
        approval = Approval(
            deployment_id=deployment_id,
            token=secrets.token_urlsafe(32),
            approved_by=approved_by,
            approved_at=_now().isoformat(),
            expires_at=(_now() + timedelta(seconds=ttl_seconds)).isoformat(),
        )
        data = _read()
        data[deployment_id] = asdict(approval)
        _write(data)
        return approval


def get(deployment_id: str) -> Approval | None:
    record = _read().get(deployment_id)
    return Approval(**record) if record else None


def consume(deployment_id: str) -> Approval:
    """Spend the approval for `deployment_id`, or refuse the deployment.

    Every rejection path raises `HumanApprovalRequired`. There is no argument,
    header, or tool parameter that bypasses it, because the caller never supplies
    the token in the first place — it is looked up here.
    """
    with _lock:
        data = _read()
        record = data.get(deployment_id)
        if record is None:
            raise HumanApprovalRequired(
                f"no human approval exists for deployment {deployment_id!r}. "
                "Production deployment requires a person to approve it in the "
                "PatchPilot UI."
            )
        approval = Approval(**record)
        if approval.consumed:
            raise HumanApprovalRequired(
                f"the approval for deployment {deployment_id!r} was already used at "
                f"{approval.consumed_at}. Approvals are single use."
            )
        if approval.expired:
            raise HumanApprovalRequired(
                f"the approval for deployment {deployment_id!r} expired at "
                f"{approval.expires_at}. Ask for approval again."
            )
        approval.consumed_at = _now().isoformat()
        data[deployment_id] = asdict(approval)
        _write(data)
        return approval


def revoke(deployment_id: str) -> bool:
    """Drop an approval, e.g. when a human rejects the deployment."""
    with _lock:
        data = _read()
        existed = data.pop(deployment_id, None) is not None
        _write(data)
        return existed


def clear() -> None:
    """Wipe the store. Used by the demo reset script and by tests."""
    with _lock:
        _write({})
        _pending_path().unlink(missing_ok=True)
