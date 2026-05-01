"""Live-path tests for the Medusa adapter.

Skipped automatically when:
  * MEDUSA_BASE_URL / MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD aren't set
  * The configured Medusa instance isn't reachable
  * Admin auth (POST /auth/user/emailpass) fails

CI stays mock-only — these only fire when the operator has run the local
Medusa stack (`make medusa-up && make medusa-bootstrap && make medusa-seed`)
and pasted the admin credentials into `backend/.env`.
"""

from __future__ import annotations

import json
import os
import unittest
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

REQUIRED_ENV = ("MEDUSA_BASE_URL", "MEDUSA_ADMIN_EMAIL", "MEDUSA_ADMIN_PASSWORD")


def _live_creds() -> tuple[str, str, str] | None:
    vals = [os.environ.get(k, "").strip() for k in REQUIRED_ENV]
    if not all(vals):
        return None
    return vals[0], vals[1], vals[2]


def _medusa_reachable(base: str, email: str, password: str) -> bool:
    try:
        req = Request(
            base.rstrip("/") + "/auth/user/emailpass",
            data=json.dumps({"email": email, "password": password}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as r:
            if r.status != 200:
                return False
            body = json.loads(r.read().decode())
            return bool(body.get("token"))
    except (URLError, OSError, ValueError):
        return False


CREDS = _live_creds()
SKIP_REASON = "Medusa live env not configured" if not CREDS else None
if CREDS and not _medusa_reachable(*CREDS):
    SKIP_REASON = f"Medusa at {CREDS[0]} not reachable / auth failed"


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class MedusaLiveSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.spine import db as spine_db

        spine_db.init_db()

    def test_sync_pulls_records_from_real_medusa(self) -> None:
        from app.integrations import registry

        result = registry.sync_system("medusa")
        self.assertNotIn("error", result, msg=result)
        body = result["result"]
        # Either "success" (uncapped, complete) or "partial" (truncated by
        # an explicit max_rows cap somewhere in the chain). Both mean the
        # adapter actually talked to Medusa — that's what this test pins.
        self.assertIn(body["status"], {"success", "partial"})
        self.assertEqual(body.get("summary", {}).get("mode"), "connected")
        domains = body.get("summary", {}).get("domains", {})
        # Seed creates 1 channel + 5 stock locations + 30 products.
        for required in ("Sales Channel", "Stock Location", "Product"):
            self.assertGreater(
                domains.get(required, 0), 0, f"expected at least one {required} row"
            )
        self.assertGreater(body["records_written"], 0)

    def test_sync_caches_external_refs_for_seeded_stock_locations(self) -> None:
        """Seed stashed retail_os_store_id in metadata; live sync should
        recover the substrate store_id as the local_id and write an
        external_ref linking it to the Medusa stock_location id.
        """
        from app.integrations import registry, store

        registry.sync_system("medusa")
        bundle = store.list_records(system_id="medusa", domain="Stock Location", limit=50)
        seeded_local_ids = {"sto-chi", "sto-dal", "sto-mia", "sto-nyc", "sto-sea"}
        recovered = {r.get("local_id") for r in bundle["external_refs"] if r.get("local_id")}
        # At least one seeded store id should have round-tripped.
        self.assertTrue(
            recovered & seeded_local_ids,
            f"no seeded store_id round-tripped to a substrate id; got {recovered!r}",
        )

    def test_adapter_reports_connected_in_registry(self) -> None:
        from app.integrations import registry

        systems = registry.list_systems()
        medusa = next((s for s in systems if s["system_id"] == "medusa"), None)
        self.assertIsNotNone(medusa, "medusa adapter missing from registry")
        self.assertEqual(medusa["mode"], "connected")


if __name__ == "__main__":
    unittest.main()
