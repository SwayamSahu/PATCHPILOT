#!/usr/bin/env python3
"""Configure the TrueForge harness for PatchPilot.

Everything the harness needs — the model provider, the sandbox provider, and the
three MCP connectors — is set up here through the API rather than by clicking
through Settings. That matters for a demo that has to be reproducible on someone
else's machine: `make setup` gets a stranger to the same state, and the
configuration is reviewable in git rather than living in a screenshot.

Idempotent. Safe to run repeatedly; existing resources are updated in place.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_servers.common.config import get  # noqa: E402

API = get("TRUEFORGE_API_BASE", "http://localhost:8790/api/v1").rstrip("/")
TIMEOUT = httpx.Timeout(30.0, connect=5.0)

MODEL_PROVIDER_NAME = "patchpilot-local"
SANDBOX_CONTEXT = 32768


def _call(method: str, path: str, **kwargs) -> httpx.Response:
    """Call the harness, turning an unreachable server into a clear failure.

    A connection error here means the harness is not running, which is an
    ordinary situation. Reporting it as a stack trace helps nobody.
    """
    try:
        return httpx.request(method, f"{API}{path}", timeout=TIMEOUT, **kwargs)
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"error: cannot reach the TrueForge harness at {API}\n"
            f"       {exc}\n"
            "       start it with 'make harness'"
        ) from None


def _ok(response: httpx.Response) -> bool:
    return response.status_code < 400


def _report(label: str, response: httpx.Response) -> bool:
    if _ok(response):
        print(f"  ✓ {label}")
        return True
    print(f"  ✗ {label}: {response.status_code} {response.text[:300]}")
    return False


def check_harness() -> bool:
    print("==> checking the harness is running")
    try:
        response = _call("GET", "/capabilities")
    except httpx.HTTPError as exc:
        print(f"  ✗ cannot reach TrueForge at {API}: {exc}")
        print("    start it with: npx @truefoundry/trueforge@latest")
        return False
    return _report(f"TrueForge reachable at {API}", response)


def configure_model_provider() -> bool:
    """Register the local Ollama endpoint as an OpenAI-compatible provider."""
    print("==> configuring the model provider")
    base_url = get("OPENAI_BASE_URL", "http://localhost:11434/v1")
    orchestrator = get("PATCHPILOT_ORCHESTRATOR_MODEL", "qwen3:8b")
    subagent = get("PATCHPILOT_SUBAGENT_MODEL", "qwen3:8b")

    def as_resource_name(model_id: str) -> str:
        # ResourceName forbids ':' and '/', which model ids commonly contain.
        return model_id.replace(":", "-").replace("/", "-").replace(".", "-").lower()

    model_ids = list(dict.fromkeys([orchestrator, subagent]))
    manifest = {
        "type": "custom",
        "name": MODEL_PROVIDER_NAME,
        "base_url": base_url,
        # Required by the OpenAI protocol, ignored by Ollama.
        "auth": {"api_key": get("OPENAI_API_KEY", "ollama") or "ollama"},
        "models": [
            {
                "model_id": model_id,
                "name": as_resource_name(model_id),
                "properties": {
                    "context_length": SANDBOX_CONTEXT,
                    "max_output_tokens": 8192,
                },
            }
            for model_id in model_ids
        ],
    }
    response = _call("PUT", "/settings/model-providers", json={"manifest": manifest})
    if response.status_code == 405:  # some builds expose create-only
        response = _call("POST", "/settings/model-providers", json={"manifest": manifest})
    return _report(f"model provider '{MODEL_PROVIDER_NAME}' -> {base_url} {model_ids}", response)


def configure_sandbox() -> bool:
    """Point the harness at Daytona for isolated execution."""
    print("==> configuring the sandbox provider")
    api_key = get("DAYTONA_API_KEY")
    if not api_key:
        print("  ! DAYTONA_API_KEY is not set — skipping.")
        print("    Sandbox execution is required for reproduction and tests.")
        return True
    # All four interval fields are required by the manifest schema, not just the
    # ones being changed from their catalogue defaults.
    manifest = {
        "type": "daytona",
        "auth": {"api_key": api_key},
        "exec_timeout_ms": 60000,
        "auto_stop_interval_in_minutes": 5,
        "auto_archive_interval_in_minutes": 60,
        "auto_delete_interval_in_minutes": 7200,
    }
    response = _call("PUT", "/settings/sandbox-providers", json={"manifest": manifest})
    if _ok(response):
        print("  ✓ Daytona sandbox provider configured")
        return True

    # Not fatal. TrueForge ships a local sandbox fallback, so isolated execution
    # still happens - just on this machine rather than in a remote VM. Say which
    # one is in play rather than letting the demo imply the stronger guarantee.
    print(f"  ! Daytona was not configured: {response.text[:200]}")
    print("    Falling back to TrueForge's local sandbox.")
    print("    For remote isolation, issue a Daytona key with sandbox and snapshot")
    print("    write permissions and re-run this script.")
    return True


CONNECTORS = [
    (
        "patchpilot-telemetry",
        "MCP_TELEMETRY_PORT",
        "8101",
        "Read-only production observability for checkout-api: health, metrics, "
        "logs, deployment history, incidents, and captured stack traces.",
    ),
    (
        "patchpilot-repository",
        "MCP_REPOSITORY_PORT",
        "8102",
        "Git and GitHub tools: read code and history, create branches, write "
        "files, run tests, and open pull requests.",
    ),
    (
        "patchpilot-deployment",
        "MCP_DEPLOYMENT_PORT",
        "8103",
        "Deployment tools. Staging is unrestricted; production deployment "
        "requires human approval and cannot be authorised by the agent.",
    ),
]


def configure_connectors() -> bool:
    """Register the three PatchPilot MCP servers as remote connectors."""
    print("==> registering MCP connectors")
    all_ok = True
    for name, port_var, default_port, description in CONNECTORS:
        url = f"http://127.0.0.1:{get(port_var, default_port)}/mcp"
        manifest = {"type": "remote", "name": name, "url": url, "description": description}
        response = _call("PUT", "/settings/mcp-servers", json={"manifest": manifest})
        if response.status_code == 405:
            response = _call("POST", "/settings/mcp-servers", json={"manifest": manifest})
        all_ok &= _report(f"{name} -> {url}", response)
    return all_ok


def verify_tools() -> bool:
    """Confirm the harness can actually reach each connector and list its tools.

    Registration only records a URL. This asks the harness to talk to each server,
    so a typo or an unstarted service is caught here rather than halfway through a
    demo.
    """
    print("==> verifying connectors are reachable")
    all_ok = True
    for name, _, _, _ in CONNECTORS:
        response = _call("GET", f"/mcp-servers/{name}/tools")
        if not _ok(response):
            print(f"  ✗ {name}: {response.status_code} {response.text[:200]}")
            all_ok = False
            continue
        payload = response.json()
        tools = payload.get("data", payload)
        if isinstance(tools, dict):
            tools = tools.get("tools", [])
        names = [t.get("name") for t in tools] if isinstance(tools, list) else []
        if not names:
            print(f"  ✗ {name}: registered but exposed no tools — is the server running?")
            all_ok = False
        else:
            print(f"  ✓ {name}: {len(names)} tools ({', '.join(names[:4])}…)")
    return all_ok


def main() -> int:
    print(f"PatchPilot — configuring TrueForge at {API}\n")
    if not check_harness():
        return 1
    steps = [configure_model_provider(), configure_sandbox(), configure_connectors()]
    reachable = verify_tools()
    print()
    if all(steps) and reachable:
        print("Harness configured.")
        return 0
    print("Configuration incomplete — see the failures above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
