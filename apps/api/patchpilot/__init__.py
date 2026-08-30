"""PatchPilot orchestrator.

Named `patchpilot` rather than `app` on purpose: the simulated checkout service
also ships a package called `app`, and two packages with the same name on the
path shadow each other depending on import order.
"""
