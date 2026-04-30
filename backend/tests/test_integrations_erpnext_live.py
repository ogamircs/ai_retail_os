"""Live-path tests for the ERPNext adapter.

Skipped automatically when:
  * ERPNEXT_BASE_URL / ERPNEXT_API_KEY / ERPNEXT_API_SECRET aren't set
  * The configured ERPNext instance isn't reachable

CI stays mock-only — these only fire when the operator has run the local
ERPNext stack (`make erpnext-up && make erpnext-bootstrap && make erpnext-seed`)
and pasted the API credentials into `backend/.env`.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


# Load credentials from project root .env / backend/.env the same way
# `app.config` does, but eagerly — the env-skip decision happens at module
# import, which runs before any app.* import in this test file.
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

REQUIRED_ENV = ("ERPNEXT_BASE_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET")


def _live_creds() -> tuple[str, str, str] | None:
    vals = [os.environ.get(k, "").strip() for k in REQUIRED_ENV]
    if not all(vals):
        return None
    return vals[0], vals[1], vals[2]


def _erpnext_reachable(base: str, key: str, secret: str) -> bool:
    try:
        req = Request(
            base.rstrip("/") + "/api/method/frappe.auth.get_logged_user",
            headers={"Authorization": f"token {key}:{secret}"},
        )
        with urlopen(req, timeout=5) as r:
            return r.status == 200
    except (URLError, OSError, ValueError):
        return False


CREDS = _live_creds()
SKIP_REASON = "ERPNext live env not configured" if not CREDS else None
if CREDS and not _erpnext_reachable(*CREDS):
    SKIP_REASON = f"ERPNext at {CREDS[0]} not reachable"


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


if __name__ == "__main__":
    unittest.main()
