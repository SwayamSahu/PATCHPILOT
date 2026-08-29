"""The deployment ledger for the checkout-api service.

A revision record is *metadata about a deployment*: what shipped, who shipped it,
when, and whether it turned out to be healthy. It deliberately holds no pricing
logic of its own.

That matters. The service always executes the real `checkout.py` on disk, so there
is exactly one copy of the defect in this repository — the one PatchPilot finds and
patches. Deploying a fix replaces that file and reloads the module, which is why
recovery after deployment is genuine rather than asserted.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Revision:
    """One deployed version of the service."""

    id: str
    summary: str
    author: str
    deployed_at_offset_min: int  # minutes before "now" when the demo is reset
    healthy: bool = True
    commit_sha: str | None = None

    @property
    def is_faulty(self) -> bool:
        return not self.healthy


# Ordered oldest to newest. 4c21 is the deployment that broke production.
REVISIONS: tuple = (
    Revision(
        id="4c18",
        summary="checkout: add currency field to order payload",
        author="priya",
        deployed_at_offset_min=340,
    ),
    Revision(
        id="4c19",
        summary="checkout: validate discount range before pricing",
        author="marcus",
        deployed_at_offset_min=215,
    ),
    Revision(
        id="4c20",
        summary="checkout: round order totals to cents",
        author="priya",
        deployed_at_offset_min=95,
    ),
    Revision(
        id="4c21",
        summary="checkout: simplify discount calculation",
        author="dev-bot",
        deployed_at_offset_min=28,
        healthy=False,
    ),
)

BASELINE_REVISION_ID = "4c21"
"""What production is running when the demo starts."""

_BY_ID = {r.id: r for r in REVISIONS}


def get_revision(revision_id: str) -> Revision:
    """Look up a revision, or raise KeyError listing the known ids."""
    try:
        return _BY_ID[revision_id]
    except KeyError:
        known = ", ".join(_BY_ID)
        raise KeyError(f"unknown revision {revision_id!r}; known: {known}") from None


def all_revisions() -> tuple:
    return REVISIONS
