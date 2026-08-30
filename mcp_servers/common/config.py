"""Environment configuration shared by the PatchPilot MCP servers."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


# Use python-dotenv rather than a hand-rolled parser. It is already a declared
# dependency, and it handles quoting, escaping, and multi-line values correctly -
# details a custom parser gets wrong quietly as configuration grows. `override`
# stays False so the real environment always wins over the file.
load_dotenv(REPO_ROOT / ".env", override=False)


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def require(name: str) -> str:
    """Fetch a required setting, failing loudly rather than half-working."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


SIMULATOR_URL = get("SIMULATOR_URL", f"http://127.0.0.1:{get('SIMULATOR_PORT', '8000')}")
PATCHPILOT_API_URL = get(
    "PATCHPILOT_API_URL", f"http://127.0.0.1:{get('PATCHPILOT_API_PORT', '8080')}"
)
