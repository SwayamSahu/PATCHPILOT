"""Environment configuration shared by the PatchPilot MCP servers."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Load `.env` if present, without overriding the real environment."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


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
