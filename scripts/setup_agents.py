#!/usr/bin/env python3
"""Create or update PatchPilot's agents in the TrueForge harness.

The agents are defined as JSON manifests under `agents/`, so what each one may
reach is reviewable in git rather than configured by hand. `${MODEL_...}`
placeholders are filled from the environment.

The tool scoping in these manifests is the safety model, not documentation:
`enable_tools` decides which tools an agent can see at all, and
`require_approval_for_tools` decides which ones suspend the loop for a human.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mcp_servers.common.config import get  # noqa: E402

API = get("TRUEFORGE_API_BASE", "http://localhost:8790/api/v1").rstrip("/")
AGENTS_DIR = REPO_ROOT / "agents"


def substitutions() -> dict:
    orchestrator = get("PATCHPILOT_ORCHESTRATOR_MODEL", "qwen3:8b")
    subagent = get("PATCHPILOT_SUBAGENT_MODEL", "qwen3:8b")
    provider = "patchpilot-local"

    def fqn(model_id: str) -> str:
        resource = model_id.replace(":", "-").replace("/", "-").replace(".", "-").lower()
        return f"{provider}/{resource}"

    return {"${MODEL_ORCHESTRATOR}": fqn(orchestrator), "${MODEL_SUBAGENT}": fqn(subagent)}


def load(path: Path) -> dict:
    raw = path.read_text()
    for placeholder, value in substitutions().items():
        raw = raw.replace(placeholder, value)
    return json.loads(raw)


def unreachable(exc: Exception) -> SystemExit:
    """Fail with a diagnostic rather than a stack trace.

    A harness that is not running is an ordinary situation, not an exceptional
    one, and forty lines of httpx traceback tells the reader nothing they can act
    on.
    """
    return SystemExit(
        f"error: cannot reach the TrueForge harness at {API}\n"
        f"       {exc}\n"
        "       start it with 'make harness' (or npx @truefoundry/trueforge@latest)"
    )


def find_agent_id(name: str):
    """Resolve a name to an agent id.

    Agents are addressed by generated id, not by name, so an update has to look
    the id up first. Posting blind returns 409 on the second run and would make
    this script single-use.
    """
    try:
        response = httpx.get(f"{API}/agents", timeout=30)
    except httpx.HTTPError as exc:
        raise unreachable(exc) from None
    if response.status_code >= 400:
        return None
    for agent in response.json().get("data", []):
        if agent.get("name") == name:
            return agent.get("id")
    return None


def apply(definition: dict) -> bool:
    name = definition["name"]
    body = {"name": name, "manifest": definition["manifest"]}
    agent_id = find_agent_id(name)
    try:
        if agent_id:
            response = httpx.put(
                f"{API}/agents/{agent_id}", json={"manifest": body["manifest"]}, timeout=30
            )
            verb = "updated"
        else:
            response = httpx.post(f"{API}/agents", json=body, timeout=30)
            verb = "created"
    except httpx.HTTPError as exc:
        raise unreachable(exc) from None
    if response.status_code < 400:
        servers = definition["manifest"].get("mcp_servers", [])
        scope = ", ".join(
            f"{s['name'].replace('patchpilot-', '')}:{'/'.join(s.get('enable_tools', ['@all']))}"
            for s in servers
        )
        gated = [
            t for s in servers for t in s.get("require_approval_for_tools", [])
        ]
        suffix = f"  [approval: {', '.join(gated)}]" if gated else ""
        print(f"  ✓ {name} {verb}  ({scope or 'no connectors'}){suffix}")
        return True
    print(f"  ✗ {name}: {response.status_code} {response.text[:300]}")
    return False


def main() -> int:
    files = sorted(AGENTS_DIR.glob("*.json"))
    if not files:
        print(f"no agent manifests found in {AGENTS_DIR}")
        return 1
    print(f"==> applying {len(files)} agent(s) to {API}")
    return 0 if all(apply(load(path)) for path in files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
