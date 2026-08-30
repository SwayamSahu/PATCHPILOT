"""HTTP client for the simulated production environment.

The MCP tools do not read the simulator's memory or import its modules; they call
it over the network exactly as a real observability integration would. That is what
keeps the integration architecture honest even though production itself is
simulated.
"""

from __future__ import annotations

import os

import httpx

from .config import SIMULATOR_URL

TIMEOUT = httpx.Timeout(10.0, connect=3.0)


class SimulatorUnavailable(RuntimeError):
    """The production environment could not be reached at all.

    A transport failure. Distinct from an error *response*: an agent that cannot
    tell "the monitoring system is down" from "that incident does not exist" will
    draw the wrong conclusion from both.
    """


class SimulatorError(RuntimeError):
    """The production environment answered, but with an error status."""

    def __init__(self, status_code: int, path: str, detail: str) -> None:
        super().__init__(f"production environment returned {status_code} for {path}: {detail}")
        self.status_code = status_code
        self.path = path
        self.detail = detail


def base_url() -> str:
    """Resolve the simulator base URL at call time.

    Read per call rather than pinned at import so tests (and a relocated
    simulator) can retarget the client without reimporting the module.
    """
    return os.environ.get("SIMULATOR_URL", SIMULATOR_URL).rstrip("/")


def _request(method: str, path: str, **kwargs) -> dict:
    url = f"{base_url()}{path}"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise SimulatorUnavailable(
            f"could not reach the production environment at {url}: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise SimulatorError(response.status_code, path, response.text[:400])
    return response.json()


def get(path: str, params: dict | None = None) -> dict:
    return _request("GET", path, params=params)


def post(path: str, payload: dict | None = None) -> dict:
    return _request("POST", path, json=payload or {})
