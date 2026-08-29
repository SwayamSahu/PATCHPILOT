"""The code production is actually running.

`checkout.py` is the *source of truth in git* — the file PatchPilot reads, patches,
and opens a pull request against. It is never modified by the running service.

What production executes is a **live working copy** under the simulator's state
directory, seeded from `checkout.py` on reset. Deploying is precisely the act of
moving new source into that working copy and reloading it.

Keeping these separate buys two things:

* the test suite and the demo can deploy repeatedly without ever mutating a
  checked-in file, and
* "deployed" means something concrete — the repo and production can legitimately
  disagree until a deployment happens, which is the situation the whole product
  is about.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

CANONICAL_PATH = Path(__file__).with_name("checkout.py")
"""The versioned source. Read-only as far as the simulator is concerned."""

_MODULE_NAME = "patchpilot_live_checkout"
_lock = threading.RLock()
_module = None


def _live_path() -> Path:
    from . import state  # imported lazily to avoid a circular import at load time

    # Keep the filename: a stack trace from production must read
    # "checkout.py:42" so it maps straight onto the file under git.
    return state.STATE_PATH.parent / "live" / "checkout.py"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"could not load live checkout module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def module():
    """Return the live checkout module, seeding it from git on first use."""
    global _module
    with _lock:
        if _module is None:
            path = _live_path()
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(CANONICAL_PATH.read_text())
            _module = _load(path)
        return _module


def deploy_source(source: str):
    """Write new source into the live working copy and reload it."""
    global _module
    with _lock:
        path = _live_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        _module = _load(path)
        return _module


def reset():
    """Roll production back to whatever `checkout.py` currently holds."""
    return deploy_source(CANONICAL_PATH.read_text())


def live_source() -> str:
    """The source production is running right now."""
    return _live_path().read_text() if _live_path().exists() else CANONICAL_PATH.read_text()
