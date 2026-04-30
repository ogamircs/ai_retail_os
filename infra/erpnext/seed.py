"""Idempotent demo seed for ERPNext.

Projects the canonical retail demo data from `backend/data/spine.db` into a
running ERPNext instance via the Frappe REST API. Safe to re-run — every
write checks for existing rows first.

Run after `make erpnext-bootstrap`:

    python infra/erpnext/seed.py

Configuration is read from `.env` / `backend/.env`:
    ERPNEXT_BASE_URL       (default http://localhost:8080)
    ERPNEXT_API_KEY        (required)
    ERPNEXT_API_SECRET     (required)
    ERPNEXT_COMPANY        (default "AI Retail OS")
    ERPNEXT_COMPANY_ABBR   (default "ARO")
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
SPINE_DB = ROOT / "backend" / "data" / "spine.db"


def _load_env() -> None:
    """Manual .env loader so the seed script has no extra dependencies."""
    for f in (ROOT / ".env", ROOT / "backend" / ".env"):
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_env()

BASE = os.environ.get("ERPNEXT_BASE_URL", "http://localhost:8080").rstrip("/")
KEY = os.environ.get("ERPNEXT_API_KEY")
SECRET = os.environ.get("ERPNEXT_API_SECRET")
COMPANY = os.environ.get("ERPNEXT_COMPANY", "AI Retail OS")
COMPANY_ABBR = os.environ.get("ERPNEXT_COMPANY_ABBR", "ARO")

if not KEY or not SECRET:
    sys.exit("ERPNEXT_API_KEY / ERPNEXT_API_SECRET not set; check backend/.env")


# ----- Frappe REST helpers --------------------------------------------------

def _request(method: str, path: str, body: dict | None = None, retries: int = 2):
    headers = {
        "Authorization": f"token {KEY}:{SECRET}",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        req = Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=30) as r:
                payload = r.read().decode()
                return r.status, (json.loads(payload) if payload else {})
        except HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {"_raw": str(e)}
        except URLError as e:
            last_err = e
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            raise
    raise last_err  # type: ignore[misc]


def get_doc(doctype: str, name: str) -> dict | None:
    status, body = _request("GET", f"/api/resource/{quote(doctype)}/{quote(name, safe='')}")
    if status == 200:
        return body.get("data")
    return None


def find_one(doctype: str, filters: list[list]) -> dict | None:
    qs = quote(json.dumps(filters))
    status, body = _request("GET", f"/api/resource/{quote(doctype)}?filters={qs}&limit_page_length=1")
    if status == 200 and body.get("data"):
        return body["data"][0]
    return None


def insert_doc(doctype: str, doc: dict) -> dict:
    status, body = _request("POST", f"/api/resource/{quote(doctype)}", doc)
    if status in (200, 201):
        return body.get("data") or body.get("message") or {}
    raise RuntimeError(f"insert {doctype} failed [{status}]: {body}")


def submit_doc(doc: dict) -> dict:
    status, body = _request("POST", "/api/method/frappe.client.submit", {"doc": doc})
    if status not in (200, 201):
        raise RuntimeError(f"submit {doc.get('doctype')} failed [{status}]: {body}")
    return body.get("message") or {}


# ----- Seed steps -----------------------------------------------------------

def ensure_company() -> None:
    if get_doc("Company", COMPANY):
        print(f"   Company {COMPANY} already present")
        return
    insert_doc(
        "Company",
        {
            "company_name": COMPANY,
            "abbr": COMPANY_ABBR,
            "default_currency": "USD",
            "country": "United States",
        },
    )
    print(f"++ Company {COMPANY} (abbr {COMPANY_ABBR})")
    # Frappe creates default warehouses asynchronously; give the queue a beat.
    time.sleep(2)


def ensure_item_groups(spine: sqlite3.Connection) -> None:
    rows = spine.execute(
        "SELECT category, display_name FROM substrate_categories "
        "WHERE lifecycle_stage != 'external ERPNext catalog'"
    ).fetchall()
    for _category, display in rows:
        if get_doc("Item Group", display):
            continue
        insert_doc(
            "Item Group",
            {
                "item_group_name": display,
                "parent_item_group": "All Item Groups",
                "is_group": 0,
            },
        )
        print(f"++ Item Group {display}")


def ensure_warehouses(spine: sqlite3.Connection) -> dict[str, str]:
    """Returns store_id → ERPNext warehouse name map."""
    rows = spine.execute("SELECT store_id, name FROM substrate_stores").fetchall()
    out: dict[str, str] = {}
    for store_id, name in rows:
        wh_name = f"{name} - {COMPANY_ABBR}"
        if not get_doc("Warehouse", wh_name):
            insert_doc(
                "Warehouse",
                {
                    "warehouse_name": name,
                    "company": COMPANY,
                    "is_group": 0,
                    "parent_warehouse": f"All Warehouses - {COMPANY_ABBR}",
                },
            )
            print(f"++ Warehouse {wh_name}")
        out[store_id] = wh_name
    return out


def ensure_suppliers(spine: sqlite3.Connection) -> None:
    vendors = [r[0] for r in spine.execute("SELECT DISTINCT vendor FROM substrate_skus")]
    for v in vendors:
        if get_doc("Supplier", v):
            continue
        insert_doc(
            "Supplier",
            {
                "supplier_name": v,
                "supplier_group": "All Supplier Groups",
                "country": "United States",
            },
        )
        print(f"++ Supplier {v}")


CUSTOMERS = [
    "Walk-In",
    "Loyal Omnichannel",
    "Vacation Planner",
    "Home Refresher",
]


def ensure_customers() -> None:
    for cname in CUSTOMERS:
        if get_doc("Customer", cname):
            continue
        insert_doc(
            "Customer",
            {
                "customer_name": cname,
                # ERPNext requires a non-group customer group; "Individual" is
                # one of the default child groups created by company setup.
                "customer_group": "Individual",
                "territory": "Rest Of The World",
                "customer_type": "Individual",
            },
        )
        print(f"++ Customer {cname}")


def ensure_brands(spine: sqlite3.Connection) -> None:
    """Brand is a free-form ERPNext doctype required when Item.brand is set."""
    vendors = [r[0] for r in spine.execute("SELECT DISTINCT vendor FROM substrate_skus")]
    for v in vendors:
        if get_doc("Brand", v):
            continue
        insert_doc("Brand", {"brand": v})
        print(f"++ Brand {v}")


def ensure_items(spine: sqlite3.Connection) -> None:
    cat_display = dict(
        spine.execute("SELECT category, display_name FROM substrate_categories").fetchall()
    )
    rows = spine.execute(
        "SELECT s.sku, s.name, s.category, s.vendor, i.price "
        "FROM substrate_skus s JOIN substrate_inventory i ON i.sku=s.sku "
        "WHERE s.category IN ("
        "  SELECT category FROM substrate_categories "
        "  WHERE lifecycle_stage != 'external ERPNext catalog'"
        ")"
    ).fetchall()
    for sku, name, category, vendor, price in rows:
        if get_doc("Item", sku):
            continue
        insert_doc(
            "Item",
            {
                "item_code": sku,
                "item_name": name,
                "item_group": cat_display.get(category, "Products"),
                "stock_uom": "Nos",
                "is_stock_item": 1,
                "include_item_in_manufacturing": 0,
                "standard_rate": price,
                "valuation_rate": price,
                "brand": vendor,
            },
        )
        print(f"++ Item {sku} ({name})")


def opening_stock(spine: sqlite3.Connection, store_warehouses: dict[str, str]) -> None:
    """One Material Receipt per store with all SKUs that have on_hand > 0."""
    inv = spine.execute(
        "SELECT store_id, sku, on_hand FROM substrate_store_inventory WHERE on_hand > 0"
    ).fetchall()
    if not inv:
        # Fallback: distribute substrate_inventory evenly across stores
        skus = spine.execute("SELECT sku, on_hand FROM substrate_inventory").fetchall()
        store_ids = list(store_warehouses)
        n = max(1, len(store_ids))
        inv = [
            (store_ids[idx % n], sku, max(1, total // n))
            for idx, (sku, total) in enumerate(skus)
            if total > 0
        ]

    by_store: dict[str, list[tuple[str, int]]] = {}
    for sid, sku, qty in inv:
        if sid not in store_warehouses:
            continue
        by_store.setdefault(sid, []).append((sku, qty))

    for sid, items in by_store.items():
        wh = store_warehouses[sid]
        marker = f"retail-os-opening:{sid}"
        if find_one("Stock Entry", [["remarks", "=", marker]]):
            print(f"   opening stock for {sid} already present")
            continue
        # Skip SKUs that aren't in ERPNext (defensive)
        items = [(sku, qty) for sku, qty in items if get_doc("Item", sku)]
        if not items:
            continue
        doc = {
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Receipt",
            "company": COMPANY,
            "remarks": marker,
            "posting_date": datetime.utcnow().date().isoformat(),
            "items": [
                {
                    "item_code": sku,
                    "qty": qty,
                    "t_warehouse": wh,
                    "basic_rate": 1.0,
                }
                for sku, qty in items
            ],
        }
        try:
            submit_doc(doc)
            print(f"++ opening stock {sid} ({len(items)} lines)")
        except Exception as e:
            print(f"!! opening stock {sid} failed: {e}")


def ensure_purchase_orders(spine: sqlite3.Connection) -> None:
    """Create one PO per substrate_inbound_pos row.

    Frappe's Purchase Order doctype does not persist a free-form `remarks`
    field, so we identify uniqueness by (supplier, schedule_date). This is
    fine because the spine seed uses one ETA per PO; if two PO rows shared
    a supplier+ETA, only one would land — acceptable for demo.
    """
    rows = spine.execute(
        "SELECT po_id, sku, vendor, eta, qty FROM substrate_inbound_pos LIMIT 12"
    ).fetchall()
    for po_id, sku, vendor, eta, qty in rows:
        if not get_doc("Item", sku):
            continue
        if not get_doc("Supplier", vendor):
            continue
        eta_date = (eta or "").split("T")[0] or datetime.utcnow().date().isoformat()
        if find_one(
            "Purchase Order",
            [["supplier", "=", vendor], ["schedule_date", "=", eta_date]],
        ):
            continue
        item = get_doc("Item", sku)
        rate = (item or {}).get("standard_rate") or 1.0
        doc = {
            "doctype": "Purchase Order",
            "supplier": vendor,
            "company": COMPANY,
            "transaction_date": datetime.utcnow().date().isoformat(),
            "schedule_date": eta_date,
            "items": [
                {
                    "item_code": sku,
                    "qty": qty,
                    "rate": rate,
                    "schedule_date": eta_date,
                    "warehouse": f"Stores - {COMPANY_ABBR}",
                }
            ],
        }
        try:
            insert_doc("Purchase Order", doc)
            print(f"++ PO {po_id} (supplier {vendor}, sku {sku}, qty {qty})")
        except Exception as e:
            print(f"!! PO {po_id} failed: {e}")


def ensure_sales_invoices(spine: sqlite3.Connection, store_warehouses: dict[str, str]) -> None:
    """Create a handful of recent demo Sales Invoices as DRAFTS.

    We deliberately leave them unsubmitted (and not is_pos=1) so the seed has
    no dependency on a POS Profile being configured. The adapter's live_sync
    in P3 can be relaxed to include drafts, or the operator can submit them
    manually from the desk.
    """
    rows = spine.execute(
        "SELECT id, ts, sku, units, revenue, store_id FROM substrate_orders "
        "ORDER BY id DESC LIMIT 10"
    ).fetchall()
    for oid, ts, sku, units, revenue, store_id in rows:
        marker = f"retail-os-order:{oid}"
        if find_one("Sales Invoice", [["remarks", "=", marker]]):
            continue
        if not get_doc("Item", sku):
            continue
        wh = store_warehouses.get(store_id) or next(iter(store_warehouses.values()), None)
        if not wh:
            continue
        rate = revenue / max(1, units)
        try:
            ts_date = (ts or "").split("T")[0] or datetime.utcnow().date().isoformat()
            doc = {
                "doctype": "Sales Invoice",
                "customer": "Walk-In",
                "company": COMPANY,
                "currency": "USD",
                "selling_price_list": "Standard Selling",
                "price_list_currency": "USD",
                "plc_conversion_rate": 1,
                "conversion_rate": 1,
                "set_warehouse": wh,
                "posting_date": ts_date,
                "due_date": (
                    datetime.fromisoformat(ts_date) + timedelta(days=14)
                ).date().isoformat(),
                "remarks": marker,
                "items": [
                    {"item_code": sku, "qty": units, "rate": rate, "warehouse": wh}
                ],
            }
            insert_doc("Sales Invoice", doc)
            print(f"++ Sales Invoice (draft) for order {oid}")
        except Exception as e:
            print(f"!! Sales Invoice {oid} failed: {e}")


def main() -> None:
    if not SPINE_DB.exists():
        sys.exit(f"spine.db not found at {SPINE_DB} — run `python -m app.substrate.seed` first")

    print(f"==> seeding ERPNext at {BASE}")
    print(f"    company={COMPANY!r} abbr={COMPANY_ABBR!r}")
    print(f"    spine_db={SPINE_DB}")
    print()

    spine = sqlite3.connect(SPINE_DB)
    spine.row_factory = sqlite3.Row

    ensure_company()
    ensure_item_groups(spine)
    store_warehouses = ensure_warehouses(spine)
    ensure_suppliers(spine)
    ensure_customers()
    ensure_brands(spine)
    ensure_items(spine)
    opening_stock(spine, store_warehouses)
    ensure_purchase_orders(spine)
    ensure_sales_invoices(spine, store_warehouses)

    print()
    print("==> done. Verify in the ERPNext desk:")
    print(f"    {BASE}/app/item-group")
    print(f"    {BASE}/app/item")
    print(f"    {BASE}/app/warehouse")
    print(f"    {BASE}/app/purchase-order")
    print(f"    {BASE}/app/sales-invoice")


if __name__ == "__main__":
    main()
