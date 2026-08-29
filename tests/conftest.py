"""Shared fixtures: a live checkout-api for the MCP integration tests."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


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
        "SIMULATOR_STATE_PATH": str(state_dir / "state.json"),
        "SIMULATOR_RECOVERY_SECONDS": "24",
        "PYTHONPATH": str(REPO_ROOT / "simulator" / "checkout-service"),
    }
    process = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
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
