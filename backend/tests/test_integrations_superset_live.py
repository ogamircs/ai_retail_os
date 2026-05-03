"""Live-path tests for the Apache Superset adapter.

Opt-in only — see `backend/tests/_live_gate.py`. Skipped unless:
  * `RUN_LIVE_TESTS=1` or `RUN_SUPERSET_LIVE=1` is set
  * SUPERSET_BASE_URL / USERNAME / PASSWORD are set
  * The login round-trip succeeds against the configured instance
"""

from __future__ import annotations

import json
import os
import unittest
from urllib.error import URLError
from urllib.request import Request, urlopen

from tests._live_gate import load_env, skip_reason

load_env()

REQUIRED_ENV = ("SUPERSET_BASE_URL", "SUPERSET_USERNAME", "SUPERSET_PASSWORD")


def _superset_reachable() -> bool:
    base = os.environ["SUPERSET_BASE_URL"].strip()
    user = os.environ["SUPERSET_USERNAME"].strip()
    pw = os.environ["SUPERSET_PASSWORD"].strip()
    try:
        body = json.dumps({
            "username": user,
            "password": pw,
            "provider": "db",
            "refresh": True,
        }).encode()
        req = Request(
            base.rstrip("/") + "/api/v1/security/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode()) if r.status == 200 else {}
            return bool(data.get("access_token"))
    except (URLError, OSError, ValueError):
        return False


SKIP_REASON = skip_reason("superset", REQUIRED_ENV, _superset_reachable)


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class SupersetLiveSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.spine import db as spine_db

        spine_db.init_db()

    def test_sync_pulls_records_from_real_superset(self) -> None:
        from app.integrations import registry

        result = registry.sync_system("superset")
        self.assertNotIn("error", result, msg=result)
        body = result["result"]
        self.assertEqual(body["status"], "success")
        self.assertEqual(body.get("summary", {}).get("mode"), "connected")
        domains = body.get("summary", {}).get("domains", {})
        # Demo seed creates 1 db connection + 3 datasets + 3 charts + 1 dashboard.
        self.assertGreater(domains.get("Database", 0), 0)
        self.assertGreater(domains.get("Dataset", 0), 0)
        self.assertGreater(domains.get("Dashboard", 0), 0)

    def test_sync_caches_external_refs_for_seeded_dashboard(self) -> None:
        from app.integrations import registry, store

        registry.sync_system("superset")
        bundle = store.list_records(system_id="superset", domain="Dashboard", limit=50)
        # The seed registers `ai-retail-os-demo` as a slug — it should
        # round-trip back as the local_id on the cached dashboard.
        recovered = {r.get("local_id") for r in bundle["external_refs"] if r.get("local_id")}
        self.assertIn(
            "ai-retail-os-demo",
            recovered,
            f"seeded dashboard slug did not round-trip; got {recovered!r}",
        )

    def test_adapter_reports_connected_in_registry(self) -> None:
        from app.integrations import registry

        systems = registry.list_systems()
        sup = next((s for s in systems if s["system_id"] == "superset"), None)
        self.assertIsNotNone(sup, "superset adapter missing from registry")
        self.assertEqual(sup["mode"], "connected")


if __name__ == "__main__":
    unittest.main()
