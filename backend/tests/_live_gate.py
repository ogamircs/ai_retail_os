"""Shared opt-in gate for live integration tests.

Live integration tests against real ERPNext / Mautic / Medusa / OpenBoxes /
Akeneo / Superset / Shopify instances are off by default. CI and stock
`python -m unittest discover` runs stay mock-only, even when credentials
happen to be present in `.env` (which is the common local-dev case).

Opt in by setting:

    RUN_LIVE_TESTS=1                 # all adapters
    RUN_<SYSTEM>_LIVE=1              # one adapter (e.g. RUN_ERPNEXT_LIVE=1)

Each live test module wires its required env keys + a reachability probe
through `skip_reason(...)` and decorates its TestCase with
`unittest.skipIf(SKIP_REASON, SKIP_REASON)`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path


_TRUTHY = {"1", "true", "yes", "on"}


def load_env() -> None:
    """Mirror `app.config`'s .env loader, eagerly, before any app.* import.

    Tests need the env populated at *module import* time so the skip
    decorator resolves correctly; we can't wait for FastAPI/app.config
    to do this lazily.
    """
    root = Path(__file__).resolve().parents[2]
    for f in (root / ".env", root / "backend" / ".env"):
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def opt_in(system: str) -> bool:
    """`RUN_LIVE_TESTS=1` opts in every adapter; `RUN_<SYSTEM>_LIVE=1` opts in one."""
    if os.environ.get("RUN_LIVE_TESTS", "").strip().lower() in _TRUTHY:
        return True
    return os.environ.get(f"RUN_{system.upper()}_LIVE", "").strip().lower() in _TRUTHY


def skip_reason(
    system: str,
    required_env: tuple[str, ...],
    reachable: Callable[[], bool] | None = None,
) -> str | None:
    """Return a human-readable skip reason, or None if the test should run.

    Order matters: opt-in is checked first so a developer with creds in
    `.env` doesn't get surprised by a live run on a stock unittest invocation.
    """
    if not opt_in(system):
        return (
            f"{system} live tests opt-in only — "
            f"set RUN_LIVE_TESTS=1 or RUN_{system.upper()}_LIVE=1"
        )
    missing = [k for k in required_env if not os.environ.get(k, "").strip()]
    if missing:
        return f"{system} live env not configured (missing: {', '.join(missing)})"
    if reachable is not None and not reachable():
        return f"{system} live instance not reachable"
    return None
