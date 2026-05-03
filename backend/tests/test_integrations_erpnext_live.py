"""Live-path tests for the ERPNext adapter.

Opt-in only — see `backend/tests/_live_gate.py`. Skipped unless:
  * `RUN_LIVE_TESTS=1` or `RUN_ERPNEXT_LIVE=1` is set
  * ERPNEXT_BASE_URL / ERPNEXT_API_KEY / ERPNEXT_API_SECRET are set
  * The configured ERPNext instance is reachable

CI and stock `python -m unittest discover` stay mock-only by default.
"""

from __future__ import annotations

import os
import unittest
from urllib.error import URLError
from urllib.request import Request, urlopen

from tests._live_gate import load_env, skip_reason

load_env()

REQUIRED_ENV = ("ERPNEXT_BASE_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET")


def _erpnext_reachable() -> bool:
    base = os.environ["ERPNEXT_BASE_URL"].strip()
    key = os.environ["ERPNEXT_API_KEY"].strip()
    secret = os.environ["ERPNEXT_API_SECRET"].strip()
    try:
        req = Request(
            base.rstrip("/") + "/api/method/frappe.auth.get_logged_user",
            headers={"Authorization": f"token {key}:{secret}"},
        )
        with urlopen(req, timeout=5) as r:
            return r.status == 200
    except (URLError, OSError, ValueError):
        return False


SKIP_REASON = skip_reason("erpnext", REQUIRED_ENV, _erpnext_reachable)


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class ERPNextLiveSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        # Import lazily so the module is importable in the env-skipped case.
        from app.spine import db as spine_db

        spine_db.init_db()

    def test_sync_pulls_records_from_real_erpnext(self) -> None:
        from app.integrations import registry

        result = registry.sync_system("erpnext")
        self.assertNotIn("error", result, msg=result)
        run = result["sync_run"]
        body = result["result"]
        self.assertEqual(run["status"], "success")
        self.assertEqual(body["status"], "success")
        self.assertGreater(body["records_written"], 0)
        domains = body.get("summary", {}).get("domains", {})
        # At minimum the seed produced Items, Warehouses, Suppliers, Purchase
        # Orders, and per-warehouse Bins. Don't assert exact counts — those
        # depend on whatever the operator has done in the desk by hand.
        for required in ("Item", "Warehouse", "Supplier"):
            self.assertGreater(
                domains.get(required, 0),
                0,
                msg=f"expected at least one {required} from live sync, got {domains}",
            )

    def test_sync_caches_external_refs_for_our_skus(self) -> None:
        from app.integrations import registry, store
        from app.spine.db import conn

        registry.sync_system("erpnext")

        with conn() as c:
            sku_rows = c.execute(
                "SELECT sku FROM substrate_skus "
                "WHERE sku LIKE 'SUM-%' OR sku LIKE 'HOM-%' OR sku LIKE 'ELE-%' "
                "LIMIT 5"
            ).fetchall()

        if not sku_rows:
            self.skipTest("no SUM/HOM/ELE SKUs in spine — run python -m app.substrate.seed first")

        for row in sku_rows:
            sku = row["sku"]
            data = store.list_records(system_id="erpnext", domain="Item", local_id=sku, limit=1)
            self.assertTrue(
                data["records"] or data["external_refs"],
                msg=f"no Item record_cache or external_ref found for {sku}",
            )

    def test_sync_aggregates_bins_into_substrate_on_hand(self) -> None:
        """Earlier behaviour overwrote substrate_inventory.on_hand with whichever
        Bin came last in the API response — a SKU split across 5 warehouses
        ended up showing only one warehouse's qty. This regression test pins
        the fix in place: the value we land on must be the SUM of all Bins
        for that SKU.
        """
        from app.integrations import registry
        from app.spine.db import conn

        result = registry.sync_system("erpnext")
        self.assertEqual(result["result"]["status"], "success")

        # Pick any Item that the seed shipped to multiple warehouses (every
        # SUM/HOM/ELE SKU does, after `make erpnext-seed`). Sum its Bin
        # actual_qty values from record_cache, compare to substrate.on_hand.
        with conn() as c:
            sample = c.execute(
                "SELECT sku FROM substrate_skus "
                "WHERE sku LIKE 'SUM-%' OR sku LIKE 'HOM-%' OR sku LIKE 'ELE-%' "
                "LIMIT 1"
            ).fetchone()
            if not sample:
                self.skipTest("no SUM/HOM/ELE SKUs in spine")
            sku = sample["sku"]

            bin_rows = c.execute(
                "SELECT payload_json FROM record_cache "
                "WHERE system_id='erpnext' AND domain='Bin' AND local_id=?",
                (sku,),
            ).fetchall()
            self.assertGreater(len(bin_rows), 0, msg=f"no Bin cache for {sku}")

            import json
            bin_total = sum(int(json.loads(r["payload_json"]).get("actual_qty") or 0) for r in bin_rows)

            on_hand_row = c.execute(
                "SELECT on_hand FROM substrate_inventory WHERE sku=?", (sku,)
            ).fetchone()
            self.assertIsNotNone(on_hand_row, msg=f"no inventory row for {sku}")
            self.assertEqual(
                on_hand_row["on_hand"],
                bin_total,
                msg=f"{sku}: substrate.on_hand should equal sum of Bins, got "
                f"{on_hand_row['on_hand']} vs Bin sum {bin_total}",
            )


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class ERPNextLiveApplyTest(unittest.TestCase):
    """P4 — operator-approved actions land as real ERPNext docs.

    Each test creates an outbox action via the omnichannel substrate, calls
    `registry.apply_outbound`, then checks ERPNext for the draft doc.
    """

    def setUp(self) -> None:
        from app.spine import db as spine_db
        from app.spine.db import conn

        spine_db.init_db()
        self.base = os.environ["ERPNEXT_BASE_URL"].strip()
        self.key = os.environ["ERPNEXT_API_KEY"].strip()
        self.secret = os.environ["ERPNEXT_API_SECRET"].strip()
        # hold_or_expedite_po flips PO rows to 'held' / 'expedited' permanently.
        # Reset the seeded POs to 'open' so each test sees a fresh substrate.
        with conn() as c:
            c.execute(
                "UPDATE substrate_inbound_pos SET status = 'open' "
                "WHERE status IN ('held', 'expedited')"
            )

    def _frappe(self, method: str, path: str, body: dict | None = None):
        import json as _json

        data = _json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": f"token {self.key}:{self.secret}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.base.rstrip("/") + path, data=data, headers=headers, method=method)
        with urlopen(req, timeout=10) as r:
            payload = r.read().decode()
        return _json.loads(payload) if payload else {}

    def test_promotion_creates_draft_pricing_rule(self) -> None:
        from app.integrations import registry
        from app.substrate import omnichannel

        promotion = omnichannel.create_promotion(
            category="summer_apparel",
            offer="P4 live test promotion",
            discount_percent=15,
            reason="P4 live test",
        )
        outbox = next(
            (e for e in promotion["external_actions"] if e["system_id"] == "erpnext"),
            None,
        )
        self.assertIsNotNone(outbox, msg=promotion)

        applied = registry.apply_outbound("erpnext", outbox["id"])
        self.assertEqual(applied.get("status"), "draft_created", msg=applied)
        result = applied.get("result") or {}
        name = result.get("external_id")
        self.assertTrue(name, msg=applied)

        # Round-trip: the doc must exist in ERPNext with our discount.
        doc = self._frappe("GET", f"/api/resource/Pricing%20Rule/{name}").get("data") or {}
        self.assertEqual(doc.get("apply_on"), "Item Group")
        self.assertEqual(doc.get("rate_or_discount"), "Discount Percentage")
        self.assertAlmostEqual(float(doc.get("discount_percentage") or 0), 15.0, places=2)

    def test_po_held_annotates_existing_purchase_order(self) -> None:
        from app.integrations import registry
        from app.substrate import omnichannel

        hold = omnichannel.hold_or_expedite_po(
            category="summer_apparel",
            mode="hold",
            reason="P4 live test",
        )
        outbox = next(
            (e for e in hold["external_actions"] if e["system_id"] == "erpnext"),
            None,
        )
        self.assertIsNotNone(outbox, msg=hold)

        applied = registry.apply_outbound("erpnext", outbox["id"])
        self.assertEqual(applied.get("status"), "draft_created", msg=applied)
        details = (applied.get("result") or {}).get("details") or {}
        annotated = details.get("annotated") or []
        # The seed produced 12 POs across our 4 vendors; we should land at
        # least one annotation for the hold action.
        self.assertGreater(len(annotated), 0, msg=details)

    def test_po_action_with_no_pos_payload_lands_in_error(self) -> None:
        """An ERPNext po_held / po_expedited apply that matches no Purchase
        Orders (empty `pos` payload, or no row matched on supplier+schedule_date)
        must land in `status=error`, not `draft_created`. Otherwise the
        operator gets a green chip claiming success even though nothing was
        written to ERPNext.
        """
        from app.integrations import registry
        from app.integrations.systems import ADAPTERS

        adapter = next(a for a in ADAPTERS if a.definition.system_id == "erpnext")
        outbox = adapter.propose_outbound(
            action_queue_id=None,
            agent="Replenishment",
            action_type="po_held",
            title="P4 noop test — empty pos payload",
            payload={"category": "summer_apparel", "pos": []},
        )
        applied = registry.apply_outbound("erpnext", outbox["id"])
        self.assertEqual(applied.get("status"), "error", msg=applied)
        result = applied.get("result") or {}
        self.assertIn("error", result, msg=result)

    def test_apply_route_returns_422_on_error_status(self) -> None:
        """The HTTP apply route must surface adapter-level error as 422 so
        the cockpit drawer renders the red chip. Before the fix the route
        returned 200 with `status=error` and the drawer treated it as
        success."""
        from fastapi.testclient import TestClient

        from app.integrations.systems import ADAPTERS
        from app.main import app

        adapter = next(a for a in ADAPTERS if a.definition.system_id == "erpnext")
        outbox = adapter.propose_outbound(
            action_queue_id=None,
            agent="Replenishment",
            action_type="po_held",
            title="P4 422 route test — empty pos payload",
            payload={"category": "summer_apparel", "pos": []},
        )
        with TestClient(app) as client:
            response = client.post(f"/api/integrations/erpnext/actions/{outbox['id']}/apply")
        self.assertEqual(response.status_code, 422, msg=response.text)
        body = response.json()
        # FastAPI HTTPException(detail=…) renders as {"detail": "..."}.
        self.assertIn("detail", body, msg=body)
        self.assertTrue(body["detail"], msg=body)

    def test_unsupported_action_type_falls_back_to_base(self) -> None:
        """Action types ERPNext doesn't know about (e.g. campaign_brief) must
        not break the apply flow — they fall back to the base adapter's
        `draft_created` path."""
        from app.integrations import registry
        from app.integrations.systems import ADAPTERS

        adapter = next(a for a in ADAPTERS if a.definition.system_id == "erpnext")
        outbox = adapter.propose_outbound(
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_brief",
            title="P4 fallback test",
            payload={"category": "summer_apparel", "offer": "fallback"},
        )
        applied = registry.apply_outbound("erpnext", outbox["id"])
        self.assertIn(applied.get("status"), {"draft_created", "applied_mock"}, msg=applied)
        # Cleanup — the fallback doesn't write to ERPNext, so just leave it.


if __name__ == "__main__":
    unittest.main()
