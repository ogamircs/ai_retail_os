"""Live-path tests for the Akeneo PIM adapter.

Opt-in only — see `backend/tests/_live_gate.py`. Skipped unless:
  * `RUN_LIVE_TESTS=1` or `RUN_AKENEO_LIVE=1` is set
  * AKENEO_BASE_URL / CLIENT_ID / SECRET / USERNAME / PASSWORD are set
  * The OAuth2 token round-trip succeeds against the configured instance
"""

from __future__ import annotations

import base64
import json
import os
import unittest
from urllib.error import URLError
from urllib.request import Request, urlopen

from tests._live_gate import load_env, skip_reason


load_env()

REQUIRED_ENV = (
    "AKENEO_BASE_URL",
    "AKENEO_CLIENT_ID",
    "AKENEO_SECRET",
    "AKENEO_USERNAME",
    "AKENEO_PASSWORD",
)


def _akeneo_reachable() -> bool:
    base = os.environ["AKENEO_BASE_URL"].strip()
    cid = os.environ["AKENEO_CLIENT_ID"].strip()
    sec = os.environ["AKENEO_SECRET"].strip()
    user = os.environ["AKENEO_USERNAME"].strip()
    pw = os.environ["AKENEO_PASSWORD"].strip()
    try:
        basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
        body = f"grant_type=password&username={user}&password={pw}".encode()
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


SKIP_REASON = skip_reason("akeneo", REQUIRED_ENV, _akeneo_reachable)


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
