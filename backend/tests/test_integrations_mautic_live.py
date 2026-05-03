"""Live-path tests for the Mautic adapter.

Opt-in only — see `backend/tests/_live_gate.py`. Skipped unless:
  * `RUN_LIVE_TESTS=1` or `RUN_MAUTIC_LIVE=1` is set
  * MAUTIC_BASE_URL / MAUTIC_USERNAME / MAUTIC_PASSWORD are set
  * The configured Mautic instance is reachable
"""

from __future__ import annotations

import os
import unittest
from base64 import b64encode
from urllib.error import URLError
from urllib.request import Request, urlopen

from tests._live_gate import load_env, skip_reason


load_env()

REQUIRED_ENV = ("MAUTIC_BASE_URL", "MAUTIC_USERNAME", "MAUTIC_PASSWORD")


def _mautic_reachable() -> bool:
    base = os.environ["MAUTIC_BASE_URL"].strip()
    user = os.environ["MAUTIC_USERNAME"].strip()
    password = os.environ["MAUTIC_PASSWORD"].strip()
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


SKIP_REASON = skip_reason("mautic", REQUIRED_ENV, _mautic_reachable)


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


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class MauticLiveApplyTest(unittest.TestCase):
    """Live-path tests for the P4 outbound apply.

    Each test creates a real outbox row, calls `apply_outbound`, and
    verifies the resulting Mautic doc round-trips. Drafts only — Mautic
    campaigns need events before publish, segments stay published-true
    by default.
    """

    def setUp(self) -> None:
        from app.spine import db as spine_db
        from app.substrate import seed

        spine_db.init_db()
        seed.seed()

    def test_campaign_launch_creates_draft_in_mautic(self) -> None:
        from app.integrations import registry, store
        from app.integrations.systems import MauticAdapter

        adapter = MauticAdapter()
        row = store.create_outbox_action(
            system_id="mautic",
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_launch",
            title="Live UAT campaign",
            external_domain="Campaign",
            payload={
                "campaign_id": "cmp-uat-live",
                "category": "summer_apparel",
                "segment_id": "seg-vacation",
                "channel": "paid social",
                "offer": "20% off",
                "budget": 5000,
                "projected_lift": 0.18,
                "projected_roi": 1.7,
            },
            configured=True,
        )
        result = registry.apply_outbound("mautic", row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertTrue(result.get("external_id"))

    def test_campaign_brief_reuses_seeded_segment(self) -> None:
        """The seed already created seg_vacation; applying a brief should
        find it via alias=eq lookup and not create a duplicate.
        """
        from app.integrations import registry, store

        row = store.create_outbox_action(
            system_id="mautic",
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_brief",
            title="Live UAT brief",
            external_domain="Segment Email",
            payload={
                "segment_id": "seg-vacation",
                "segment_name": "Vacation planners",
                "category": "summer_apparel",
                "channel": "paid social",
                "offer": "20% off",
            },
            configured=True,
        )
        result = registry.apply_outbound("mautic", row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        details = result.get("result", {}).get("details") or {}
        self.assertTrue(details.get("reused"), f"expected reused=True, got {details}")


if __name__ == "__main__":
    unittest.main()
