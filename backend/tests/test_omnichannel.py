import tempfile
import unittest
from pathlib import Path

from app.spine import db
from app.substrate import omnichannel, seed


class OmnichannelSubstrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_seed_creates_linked_retail_entities(self):
        self.assertEqual(len(omnichannel.list_categories()), 3)
        self.assertEqual(len(omnichannel.list_stores()), 5)
        self.assertGreaterEqual(len(omnichannel.list_campaigns()), 1)
        health = omnichannel.inventory_health()
        self.assertGreater(len(health["skus"]), 20)
        self.assertGreater(len(health["inbound_pos"]), 5)

    def test_marketing_campaign_launch_and_measurement(self):
        rec = omnichannel.recommend_category_push()
        brief = omnichannel.create_campaign_brief(rec["category"])
        self.assertIn("artifact_id", brief)
        self.assertEqual(omnichannel.list_action_queue()[0]["artifact_id"], brief["artifact_id"])
        result = omnichannel.launch_mock_campaign(
            category=rec["category"],
            segment_id=rec["segment_id"],
            channel=rec["channel"],
            budget=rec["budget"],
            offer=rec["offer"],
            projected_lift=rec["projected_lift"],
            projected_roi=rec["projected_roi"],
            title="Unit Test Category Push",
        )
        self.assertIn("campaign_id", result)
        measured = omnichannel.measure_campaign(result["campaign_id"])
        self.assertEqual(measured["status"], "measured")
        self.assertGreater(measured["actual_roi"], 0)

    def test_policy_blocks_oversized_campaign_budget(self):
        result = omnichannel.launch_mock_campaign(
            category="summer_apparel",
            segment_id="seg-vacation",
            channel="paid social",
            budget=999999,
            offer="too rich",
            projected_lift=0.5,
            projected_roi=1.1,
        )
        self.assertTrue(result["blocked"])
        self.assertEqual(omnichannel.list_action_queue()[0]["status"], "approval_required")

    def test_operational_actions_enter_queue(self):
        hold = omnichannel.hold_or_expedite_po("summer_apparel", mode="hold", reason="overstock")
        transfer = omnichannel.allocate_inventory("summer_apparel")
        routing = omnichannel.route_fulfillment("summer_apparel")
        self.assertEqual(hold["mode"], "hold")
        self.assertEqual(transfer["action"], "store_transfer")
        self.assertEqual(routing["action"], "fulfillment_routing")
        self.assertGreaterEqual(len(omnichannel.list_action_queue()), 4)


if __name__ == "__main__":
    unittest.main()
