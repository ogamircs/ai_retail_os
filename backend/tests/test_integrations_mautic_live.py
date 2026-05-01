"""Live-path tests for the Mautic adapter.

Skipped automatically when:
  * MAUTIC_BASE_URL / MAUTIC_USERNAME / MAUTIC_PASSWORD aren't set
  * The configured Mautic instance isn't reachable

CI stays mock-only — these only fire when the operator has run the local
Mautic stack (`make mautic-up && make mautic-bootstrap && make mautic-seed`)
and pasted the API credentials into `backend/.env`.
"""

from __future__ import annotations

import os
import unittest
from base64 import b64encode
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

REQUIRED_ENV = ("MAUTIC_BASE_URL", "MAUTIC_USERNAME", "MAUTIC_PASSWORD")


def _live_creds() -> tuple[str, str, str] | None:
    vals = [os.environ.get(k, "").strip() for k in REQUIRED_ENV]
    if not all(vals):
        return None
    return vals[0], vals[1], vals[2]


def _mautic_reachable(base: str, user: str, password: str) -> bool:
    try:
        token = b64encode(f"{user}:{password}".encode()).decode()
        req = Request(
            base.rstrip("/") + "/api/contacts?limit=1",
            headers={"Authorization": f"Basic {token}"},
        )
        with urlopen(req, timeout=5) as r:
            return r.status == 200
    except (URLError, OSError, ValueError):
        return False


CREDS = _live_creds()
SKIP_REASON = "Mautic live env not configured" if not CREDS else None
if CREDS and not _mautic_reachable(*CREDS):
    SKIP_REASON = f"Mautic at {CREDS[0]} not reachable"


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class MauticLiveSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.spine import db as spine_db

        spine_db.init_db()

    def test_sync_pulls_records_from_real_mautic(self) -> None:
        from app.integrations import registry

        result = registry.sync_system("mautic")
        self.assertNotIn("error", result, msg=result)
        body = result["result"]
        self.assertEqual(body["status"], "success")
        self.assertEqual(body.get("summary", {}).get("mode"), "connected")
        # The seed produces 4 segments + 20 contacts + N campaigns minimum.
        # Don't assert exact counts — operator may have created extras.
        domains = body.get("summary", {}).get("domains", {})
        for required in ("Segment", "Contact", "Campaign"):
            self.assertGreater(
                domains.get(required, 0), 0, f"expected at least one {required} row"
            )
        self.assertGreater(body["records_written"], 0)

    def test_sync_caches_external_refs_for_seeded_segments(self) -> None:
        """The seed creates aliases like seg_loyalists; the live sync should
        recover seg-loyalists as the substrate-side local_id and write an
        external_ref linking it to the Mautic list id.
        """
        from app.integrations import registry, store

        registry.sync_system("mautic")
        bundle = store.list_records(system_id="mautic", domain="Segment", limit=50)
        # At least one of the seeded segments should round-trip to its
        # spine-side id via the alias->local_id mapping in _live_sync.
        seeded_local_ids = {
            "seg-loyalists",
            "seg-vacation",
            "seg-home",
            "seg-tech",
        }
        recovered = {r.get("local_id") for r in bundle["external_refs"] if r.get("local_id")}
        self.assertTrue(
            recovered & seeded_local_ids,
            f"no seeded segment alias round-tripped to a substrate id; got {recovered!r}",
        )

    def test_adapter_reports_connected_in_registry(self) -> None:
        from app.integrations import registry

        systems = registry.list_systems()
        mautic = next((s for s in systems if s["system_id"] == "mautic"), None)
        self.assertIsNotNone(mautic, "mautic adapter missing from registry")
        self.assertEqual(mautic["mode"], "connected")


if __name__ == "__main__":
    unittest.main()
