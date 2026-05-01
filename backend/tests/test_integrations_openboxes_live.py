"""Live-path tests for the OpenBoxes adapter.

Skipped automatically when:
  * OPENBOXES_BASE_URL isn't set, or
  * neither OPENBOXES_API_TOKEN nor (OPENBOXES_USERNAME + OPENBOXES_PASSWORD) is set, or
  * the configured OpenBoxes instance isn't reachable / login fails.

CI stays mock-only — these only fire when the operator has run the local
OpenBoxes stack (`make openboxes-up && make openboxes-bootstrap && make openboxes-seed`)
and pasted the credentials into `backend/.env`.
"""

from __future__ import annotations

import json
import os
import unittest
from base64 import b64encode  # noqa: F401  (kept for symmetry with other live tests)
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def _load_env() -> None:
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


_load_env()


def _live_creds() -> tuple[str, dict[str, str]] | None:
    base = os.environ.get("OPENBOXES_BASE_URL", "").strip()
    if not base:
        return None
    token = os.environ.get("OPENBOXES_API_TOKEN", "").strip()
    if token:
        return base, {"X-Auth-Token": token}
    user = os.environ.get("OPENBOXES_USERNAME", "").strip()
    pw = os.environ.get("OPENBOXES_PASSWORD", "").strip()
    if user and pw:
        return base, {}  # bootstrap login flow handles auth
    return None


def _openboxes_reachable(base: str) -> bool:
    try:
        with urlopen(base.rstrip("/") + "/", timeout=5) as r:
            return r.status < 500
    except (URLError, OSError, ValueError):
        return False


CREDS = _live_creds()
SKIP_REASON = "OpenBoxes live env not configured" if not CREDS else None
if CREDS and not _openboxes_reachable(CREDS[0]):
    SKIP_REASON = f"OpenBoxes at {CREDS[0]} not reachable"


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
