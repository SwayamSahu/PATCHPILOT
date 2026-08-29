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
_STAGING_MODULE_NAME = "patchpilot_staging_checkout"
_lock = threading.RLock()
_module = None
_staging_module = None


def _live_path() -> Path:
    from . import state  # imported lazily to avoid a circular import at load time

    # Keep the filename: a stack trace from production must read
    # "checkout.py:42" so it maps straight onto the file under git.
    return state.STATE_PATH.parent / "live" / "checkout.py"


def _load(path: Path, module_name: str = _MODULE_NAME):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"could not load live checkout module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
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


class InvalidDeployment(RuntimeError):
    """The proposed source could not be loaded, so it was not deployed."""


def deploy_source(source: str):
    """Validate new source, then publish it to the live working copy.

    The source is loaded from a staging file *before* it replaces the live one.
    Writing first and importing afterwards would leave a broken file on disk when
    the import fails: the current process would keep serving the old module and
    look fine, while the next restart would load the invalid file and break
    checkout until someone reset the world. Validating first means a bad
    deployment changes nothing at all.
    """
    global _module
    with _lock:
        path = _live_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name("checkout.staging.py")
        staging.write_text(source)
        try:
            candidate = _load(staging)
        except Exception as exc:
            staging.unlink(missing_ok=True)
            raise InvalidDeployment(f"proposed source could not be loaded: {exc}") from exc
        finally:
            sys.modules.pop(_MODULE_NAME, None)

        for attr in ("Cart", "checkout", "compute_total"):
            if not hasattr(candidate, attr):
                staging.unlink(missing_ok=True)
                raise InvalidDeployment(f"proposed source does not define {attr!r}")

        previous = path.read_text() if path.exists() else None
        staging.replace(path)  # atomic publish
        try:
            _module = _load(path)
        except Exception as exc:
            # Validating from the staging path is not a guarantee: source whose
            # behaviour depends on __file__ can load there and fail here. Roll the
            # previous file back so a failed deployment leaves nothing behind.
            if previous is not None:
                path.write_text(previous)
                _module = _load(path)
            raise InvalidDeployment(
                f"source loaded during validation but failed once published: {exc}"
            ) from exc
        return _module


def reset():
    """Roll production back to whatever `checkout.py` currently holds."""
    return deploy_source(CANONICAL_PATH.read_text())


def live_source() -> str:
    """The source production is running right now."""
    return _live_path().read_text() if _live_path().exists() else CANONICAL_PATH.read_text()


def verify_healthy() -> bool:
    """Ask the live code whether it actually works, rather than trusting a flag.

    A deployment is healthy if the code now running prices carts correctly - both
    the zero-discount case that raises under the defect and a discounted case that
    the defect silently overcharges. Deriving the flag from a smoke test closes the
    hole where a deployment could record itself healthy and make telemetry report
    recovery while production kept serving the broken module.
    """
    checkout = module()
    try:
        if checkout.compute_total(checkout.Cart(subtotal=100.0, discount=0.0)) != 100.0:
            return False
        return checkout.compute_total(checkout.Cart(subtotal=100.0, discount=0.25)) == 75.0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------
#
# Staging is a genuinely separate environment, not a label. It has its own
# source file and its own loaded module, and nothing that happens here touches
# what production is running.
#
# This is not a stylistic choice. Staging deploys are the one write an agent may
# perform without human approval, on the grounds that they are reversible. If a
# staging deploy could reach production, that reasoning collapses and the
# approval gate can be walked straight around by calling the unguarded tool.


def _staging_path() -> Path:
    from . import state

    return state.STATE_PATH.parent / "staging" / "checkout.py"


def staging_module():
    """The module the staging environment is running, seeded from production."""
    global _staging_module
    with _lock:
        if _staging_module is None:
            path = _staging_path()
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(live_source())
            _staging_module = _load(path, _STAGING_MODULE_NAME)
        return _staging_module


def deploy_staging_source(source: str):
    """Publish source to staging only. Production is untouched."""
    global _staging_module
    with _lock:
        path = _staging_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path = path.with_name("checkout.candidate.py")
        candidate_path.write_text(source)
        try:
            candidate = _load(candidate_path, _STAGING_MODULE_NAME)
        except Exception as exc:
            candidate_path.unlink(missing_ok=True)
            raise InvalidDeployment(f"proposed source could not be loaded: {exc}") from exc
        for attr in ("Cart", "checkout", "compute_total"):
            if not hasattr(candidate, attr):
                candidate_path.unlink(missing_ok=True)
                raise InvalidDeployment(f"proposed source does not define {attr!r}")
        candidate_path.replace(path)
        _staging_module = _load(path, _STAGING_MODULE_NAME)
        return _staging_module


def verify_staging_healthy() -> bool:
    """Smoke-test the staging code, exactly as production is smoke-tested."""
    checkout = staging_module()
    try:
        if checkout.compute_total(checkout.Cart(subtotal=100.0, discount=0.0)) != 100.0:
            return False
        return checkout.compute_total(checkout.Cart(subtotal=100.0, discount=0.25)) == 75.0
    except Exception:
        return False


def reset_staging() -> None:
    global _staging_module
    with _lock:
        _staging_module = None
        path = _staging_path()
        if path.exists():
            path.unlink()
