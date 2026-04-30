import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.spine import db
from app.integrations import registry
from app.substrate import omnichannel, seed


INTEGRATION_ENV_KEYS = [
    "ERPNEXT_BASE_URL",
    "ERPNEXT_API_KEY",
    "ERPNEXT_API_SECRET",
    "MAUTIC_BASE_URL",
    "MAUTIC_USERNAME",
    "MAUTIC_PASSWORD",
    "MAUTIC_BASIC_TOKEN",
    "MEDUSA_BASE_URL",
    "MEDUSA_API_KEY",
    "OPENBOXES_BASE_URL",
    "OPENBOXES_USERNAME",
    "OPENBOXES_PASSWORD",
    "AKENEO_BASE_URL",
    "AKENEO_CLIENT_ID",
    "AKENEO_SECRET",
    "AKENEO_USERNAME",
    "AKENEO_PASSWORD",
    "SUPERSET_BASE_URL",
]


class IntegrationLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        self.old_env = {key: os.environ.get(key) for key in INTEGRATION_ENV_KEYS}
        for key in INTEGRATION_ENV_KEYS:
            os.environ.pop(key, None)
        seed.seed()

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_system_registry_defaults_to_mock_mode(self):
        systems = registry.list_systems()
        self.assertEqual(len(systems), 6)
        self.assertIn("erpnext", {system["system_id"] for system in systems})
        self.assertTrue(all(system["mode"] == "mock" for system in systems))
        self.assertTrue(all(system["configured"] is False for system in systems))

    def test_mock_erpnext_sync_creates_cache_and_external_refs(self):
        result = registry.sync_system("erpnext")
        self.assertEqual(result["result"]["status"], "success")
        self.assertGreater(result["result"]["records_written"], 0)
        records = registry.list_records(system_id="erpnext", domain="Item")
        self.assertGreater(len(records["records"]), 0)
        self.assertGreater(len(records["external_refs"]), 0)
        systems = {system["system_id"]: system for system in registry.list_systems()}
        self.assertIsNotNone(systems["erpnext"]["last_sync_ts"])

    def test_action_tools_create_approval_gated_outbox_actions(self):
        promotion = omnichannel.create_promotion(
            category="summer_apparel",
            offer="25% member summer edit",
            discount_percent=25,
            reason="test",
        )
        self.assertIn("external_actions", promotion)
        self.assertEqual(promotion["external_actions"][0]["system_id"], "erpnext")
        self.assertEqual(promotion["external_actions"][0]["status"], "mock_only")
        actions = omnichannel.list_action_queue()
        self.assertGreaterEqual(len(actions[0]["external_actions"]), 1)

    def test_apply_outbound_action_is_manual_and_mock_safe(self):
        promotion = omnichannel.create_promotion(
            category="summer_apparel",
            offer="25% member summer edit",
            discount_percent=25,
            reason="test",
        )
        outbox = promotion["external_actions"][0]
        with TestClient(app) as client:
            response = client.post(f"/api/integrations/erpnext/actions/{outbox['id']}/apply")
        self.assertEqual(response.status_code, 200)
        applied = response.json()
        self.assertEqual(applied["status"], "applied_mock")
        self.assertIn("no external system was mutated", applied["result"]["message"])

    def test_coerce_id_rejects_missing_and_blank(self):
        """Pin the malformed-id guard: a missing/blank Mautic id must NOT
        cache rows under str(None) == "None" — otherwise multiple bad rows
        collide on a fake external_id and corrupt the cache.
        """
        from app.integrations.systems import _coerce_id

        self.assertIsNone(_coerce_id(None))
        self.assertIsNone(_coerce_id(""))
        self.assertIsNone(_coerce_id("   "))
        self.assertIsNone(_coerce_id(True))   # booleans are not valid ids
        self.assertEqual(_coerce_id(42), "42")
        self.assertEqual(_coerce_id("42"), "42")
        self.assertEqual(_coerce_id("  abc  "), "abc")

    def test_mautic_webhook_is_recorded_as_measurement(self):
        payload = {"campaign_id": "cmp-weekend-heat", "event": "email.open", "count": 12}
        with TestClient(app) as client:
            response = client.post("/api/integrations/mautic/webhook", json=payload)
            records = client.get("/api/integrations/records?system=mautic&domain=Webhook%20Event")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(records.status_code, 200)
        self.assertEqual(records.json()["records"][0]["payload"]["event"], "email.open")


if __name__ == "__main__":
    unittest.main()
