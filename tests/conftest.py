"""Shared fixtures: a live checkout-api for the MCP integration tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _python() -> str:
    """The interpreter to launch the simulator with.

    Prefer the repository's own virtualenv, but fall back to the interpreter
    running the tests. A fresh clone has no .venv, and hard-coding that path made
    every subprocess-based test error out there - including when an agent checks
    out the repo and runs the suite to verify its own fix.
    """
    candidate = REPO_ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_simulator(tmp_path_factory):
    """Run the real checkout-api in a subprocess and yield its base URL.

    The MCP servers talk to production over HTTP, so the tests that exercise them
    must do the same. Importing the app in-process would test a different code
    path than the one that ships.
    """
    port = _free_port()
    state_dir = tmp_path_factory.mktemp("simstate")
    env = {
        **os.environ,
        # Seed production from a faulty baseline the tests own, not from the
        # versioned checkout.py. These tests assert production starts broken, so
        # reading the live file would make them fail the moment the bug is fixed —
        # which is exactly what the agent is supposed to do, and what the workflow
        # needs a green suite to confirm.
        "SIMULATOR_BASELINE_SOURCE": str(REPO_ROOT / "tests" / "fixtures" / "checkout_faulty.py"),
        "SIMULATOR_STATE_PATH": str(state_dir / "state.json"),
        "SIMULATOR_RECOVERY_SECONDS": "24",
        "PYTHONPATH": str(REPO_ROOT / "simulator" / "checkout-service"),
    }
    process = subprocess.Popen(
        [_python(), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(REPO_ROOT / "simulator" / "checkout-service"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "simulator exited during startup: "
                    + (process.stderr.read().decode()[-800:] if process.stderr else "")
                )
            try:
                if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.15)
        else:
            raise RuntimeError("simulator did not become healthy within 30s")

        os.environ["SIMULATOR_URL"] = base
        httpx.post(f"{base}/_control/reset", timeout=5.0)
        yield base
    finally:
        os.environ.pop("SIMULATOR_URL", None)
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
