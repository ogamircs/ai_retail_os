"""Live-path tests for the OpenBoxes adapter.

Opt-in only — see `backend/tests/_live_gate.py`. Skipped unless:
  * `RUN_LIVE_TESTS=1` or `RUN_OPENBOXES_LIVE=1` is set
  * OPENBOXES_BASE_URL is set, plus either OPENBOXES_API_TOKEN or
    OPENBOXES_USERNAME+OPENBOXES_PASSWORD
  * The configured OpenBoxes instance is reachable
"""

from __future__ import annotations

import json
import os
import unittest
from urllib.error import URLError
from urllib.request import urlopen

from tests._live_gate import load_env, opt_in


load_env()


def _has_creds() -> tuple[bool, str]:
    base = os.environ.get("OPENBOXES_BASE_URL", "").strip()
    if not base:
        return False, "OPENBOXES_BASE_URL"
    token = os.environ.get("OPENBOXES_API_TOKEN", "").strip()
    user = os.environ.get("OPENBOXES_USERNAME", "").strip()
    pw = os.environ.get("OPENBOXES_PASSWORD", "").strip()
    if not token and not (user and pw):
        return False, "OPENBOXES_API_TOKEN or OPENBOXES_USERNAME+OPENBOXES_PASSWORD"
    return True, base


def _openboxes_reachable(base: str) -> bool:
    try:
        with urlopen(base.rstrip("/") + "/", timeout=5) as r:
            return r.status < 500
    except (URLError, OSError, ValueError):
        return False


def _compute_skip_reason() -> str | None:
    if not opt_in("openboxes"):
        return (
            "openboxes live tests opt-in only — "
            "set RUN_LIVE_TESTS=1 or RUN_OPENBOXES_LIVE=1"
        )
    ok, info = _has_creds()
    if not ok:
        return f"OpenBoxes live env not configured (missing: {info})"
    if not _openboxes_reachable(info):
        return f"OpenBoxes at {info} not reachable"
    return None


SKIP_REASON = _compute_skip_reason()


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class OpenBoxesLiveSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.spine import db as spine_db

        spine_db.init_db()

    def test_sync_pulls_records_from_real_openboxes(self) -> None:
        from app.integrations import registry

        result = registry.sync_system("openboxes")
        self.assertNotIn("error", result, msg=result)
        body = result["result"]
        self.assertEqual(body["status"], "success")
        self.assertEqual(body.get("summary", {}).get("mode"), "connected")
        domains = body.get("summary", {}).get("domains", {})
        # Seed creates at least Locations + Products. Inbound Shipments
        # may be 0 on a fresh demo (no PO has been imported yet) — gate
        # is "key present in summary", not "count > 0".
        self.assertGreater(domains.get("Location", 0), 0, "expected at least one Location")
        self.assertGreater(domains.get("Product", 0), 0, "expected at least one Product")

    def test_sync_caches_external_refs_for_seeded_locations(self) -> None:
        """Seed embeds [retail-os:<store_id>] in description; live sync
        should recover the substrate store_id as the local_id and write
        an external_ref linking it to the OpenBoxes location id.
        """
        from app.integrations import registry, store

        registry.sync_system("openboxes")
        bundle = store.list_records(system_id="openboxes", domain="Location", limit=50)
        seeded_local_ids = {"sto-chi", "sto-dal", "sto-mia", "sto-nyc", "sto-sea"}
        recovered = {r.get("local_id") for r in bundle["external_refs"] if r.get("local_id")}
        self.assertTrue(
            recovered & seeded_local_ids,
            f"no seeded store_id round-tripped to a substrate id; got {recovered!r}",
        )

    def test_adapter_reports_connected_in_registry(self) -> None:
        from app.integrations import registry

        systems = registry.list_systems()
        ob = next((s for s in systems if s["system_id"] == "openboxes"), None)
        self.assertIsNotNone(ob, "openboxes adapter missing from registry")
        self.assertEqual(ob["mode"], "connected")


if __name__ == "__main__":
    unittest.main()
