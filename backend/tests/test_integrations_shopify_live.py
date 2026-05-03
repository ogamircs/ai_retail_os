"""Live-path tests for the Shopify Plus adapter.

Skipped automatically when:
  * SHOPIFY_SHOP_DOMAIN / SHOPIFY_ADMIN_TOKEN aren't set
  * The configured Shopify dev store isn't reachable / token is invalid

CI stays mock-only — these only fire when the operator has provisioned a
Shopify Partner dev store, minted a custom-app token, run `make shopify-seed`,
and pasted credentials into `backend/.env`.
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

REQUIRED_ENV = ("SHOPIFY_SHOP_DOMAIN", "SHOPIFY_ADMIN_TOKEN")


def _live_creds() -> tuple[str, str, str] | None:
    domain = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip()
    token = os.environ.get("SHOPIFY_ADMIN_TOKEN", "").strip()
    version = os.environ.get("SHOPIFY_API_VERSION", "2025-01").strip() or "2025-01"
    if not domain or not token:
        return None
    return domain, token, version


def _shopify_reachable(domain: str, token: str, version: str) -> bool:
    try:
        body = json.dumps({"query": "{ shop { name } }"}).encode()
        req = Request(
            f"https://{domain}/admin/api/{version}/graphql.json",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": token,
            },
            method="POST",
        )
        with urlopen(req, timeout=5) as r:
            if r.status != 200:
                return False
            data = json.loads(r.read().decode() or "{}")
            return bool(((data.get("data") or {}).get("shop") or {}).get("name"))
    except (URLError, OSError, ValueError):
        return False


CREDS = _live_creds()
SKIP_REASON = "Shopify live env not configured" if not CREDS else None
if CREDS and not _shopify_reachable(*CREDS):
    SKIP_REASON = f"Shopify at {CREDS[0]} not reachable / token invalid"


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class ShopifyLiveSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.spine import db as spine_db

        spine_db.init_db()

    def test_sync_pulls_records_from_real_shopify(self) -> None:
        from app.integrations import registry

        result = registry.sync_system("shopify")
        self.assertNotIn("error", result, msg=result)
        body = result["result"]
        self.assertIn(body["status"], {"success", "partial"})
        self.assertEqual(body.get("summary", {}).get("mode"), "connected")
        domains = body.get("summary", {}).get("domains", {})
        # Seed creates 5 locations + 30 products at minimum.
        for required in ("Location", "Product"):
            self.assertGreater(
                domains.get(required, 0), 0, f"expected at least one {required} row"
            )

    def test_sync_caches_external_refs_for_seeded_locations(self) -> None:
        """The seed stashed `retail_os.store_id` metafields on locations;
        live sync should recover those as local_ids and write external_refs.
        """
        from app.integrations import registry, store

        registry.sync_system("shopify")
        bundle = store.list_records(system_id="shopify", domain="Location", limit=50)
        seeded_local_ids = {"sto-chi", "sto-dal", "sto-mia", "sto-nyc", "sto-sea"}
        recovered = {r.get("local_id") for r in bundle["external_refs"] if r.get("local_id")}
        self.assertTrue(
            recovered & seeded_local_ids,
            f"no seeded store_id round-tripped to a substrate id; got {recovered!r}",
        )

    def test_adapter_reports_connected_in_registry(self) -> None:
        from app.integrations import registry

        systems = registry.list_systems()
        shopify = next((s for s in systems if s["system_id"] == "shopify"), None)
        self.assertIsNotNone(shopify, "shopify adapter missing from registry")
        self.assertEqual(shopify["mode"], "connected")


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class ShopifyLiveApplyTest(unittest.TestCase):
    """Live-path tests for the P4 outbound apply.

    Each test creates a real outbox row and calls `apply_outbound`. The
    promotion test creates a real automatic discount in the dev store —
    operator can revoke it from Discounts → Automatic discounts. The
    campaign_brief test lands a Shop-level metafield (idempotent via
    payload-hash marker, so re-runs don't pile up entries).
    """

    def setUp(self) -> None:
        from app.spine import db as spine_db
        from app.substrate import seed

        spine_db.init_db()
        seed.seed()

    def test_promotion_creates_draft_discount_in_dev_store(self) -> None:
        from app.integrations import registry, store

        row = store.create_outbox_action(
            system_id="shopify",
            action_queue_id=None,
            agent="Pricing & Promo",
            action_type="promotion",
            title="UAT — Summer apparel 25% off",
            external_domain="Discount",
            payload={"category": "summer_apparel", "discount_pct": 0.25},
            configured=True,
        )
        result = registry.apply_outbound("shopify", row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertTrue(result.get("external_id"))

    def test_campaign_brief_stashes_on_shop_metafield(self) -> None:
        from app.integrations import registry, store

        row = store.create_outbox_action(
            system_id="shopify",
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_brief",
            title="UAT — Weekend heatwave",
            external_domain="Campaign",
            payload={"category": "summer_apparel", "segment_id": "seg_vacation"},
            configured=True,
        )
        result = registry.apply_outbound("shopify", row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertTrue(result.get("external_id"))


if __name__ == "__main__":
    unittest.main()
