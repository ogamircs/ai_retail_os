"""Live-path tests for the Akeneo PIM adapter.

Skipped automatically when:
  * AKENEO_BASE_URL / CLIENT_ID / SECRET / USERNAME / PASSWORD aren't set
  * The configured Akeneo instance isn't reachable
  * OAuth2 token round-trip fails

CI stays mock-only — these only fire when the operator has run the
local Akeneo stack (`make akeneo-up && make akeneo-bootstrap && make akeneo-seed`)
and pasted the credentials into `backend/.env`.
"""

from __future__ import annotations

import base64
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

REQUIRED_ENV = (
    "AKENEO_BASE_URL",
    "AKENEO_CLIENT_ID",
    "AKENEO_SECRET",
    "AKENEO_USERNAME",
    "AKENEO_PASSWORD",
)


def _live_creds() -> tuple[str, str, str, str, str] | None:
    vals = [os.environ.get(k, "").strip() for k in REQUIRED_ENV]
    if not all(vals):
        return None
    return tuple(vals)  # type: ignore[return-value]


def _akeneo_reachable(base: str, cid: str, sec: str, user: str, pw: str) -> bool:
    try:
        basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
        body = (
            f"grant_type=password&username={user}&password={pw}".encode()
        )
        req = Request(
            base.rstrip("/") + "/api/oauth/v1/token",
            data=body,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urlopen(req, timeout=5) as r:
            return r.status == 200 and bool(json.loads(r.read().decode()).get("access_token"))
    except (URLError, OSError, ValueError):
        return False


CREDS = _live_creds()
SKIP_REASON = "Akeneo live env not configured" if not CREDS else None
if CREDS and not _akeneo_reachable(*CREDS):
    SKIP_REASON = f"Akeneo at {CREDS[0]} not reachable / auth failed"


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class AkeneoLiveSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.spine import db as spine_db

        spine_db.init_db()

    def test_sync_pulls_records_from_real_akeneo(self) -> None:
        from app.integrations import registry

        result = registry.sync_system("akeneo")
        self.assertNotIn("error", result, msg=result)
        body = result["result"]
        self.assertEqual(body["status"], "success")
        self.assertEqual(body.get("summary", {}).get("mode"), "connected")
        domains = body.get("summary", {}).get("domains", {})
        self.assertGreater(domains.get("Category", 0), 0)
        self.assertGreater(domains.get("Product", 0), 0)

    def test_sync_caches_external_refs_for_seeded_categories(self) -> None:
        from app.integrations import registry, store

        registry.sync_system("akeneo")
        bundle = store.list_records(system_id="akeneo", domain="Category", limit=50)
        # The seed creates substrate categories — codes should round-trip
        # straight back as local_id (Akeneo uses code as identifier).
        seeded_local_ids = {"summer_apparel", "tech_accessories", "home_goods"}
        recovered = {r.get("local_id") for r in bundle["external_refs"] if r.get("local_id")}
        self.assertTrue(
            recovered & seeded_local_ids,
            f"no seeded category code round-tripped; got {recovered!r}",
        )

    def test_adapter_reports_connected_in_registry(self) -> None:
        from app.integrations import registry

        systems = registry.list_systems()
        akeneo = next((s for s in systems if s["system_id"] == "akeneo"), None)
        self.assertIsNotNone(akeneo, "akeneo adapter missing from registry")
        self.assertEqual(akeneo["mode"], "connected")


if __name__ == "__main__":
    unittest.main()
