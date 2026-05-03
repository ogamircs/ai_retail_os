"""Idempotent demo seed for Shopify Plus dev stores.

Projects the canonical retail demo data from `backend/data/spine.db` into a
Shopify dev store via the Admin GraphQL API. Safe to re-run — every write
checks for an existing row first via variant SKU (products), `retail_os.store_id`
metafield (locations), or `retail-os-seed:<order_id>` tag (draft orders).

What gets created (against an empty store):
  - 30 Products    (one per `substrate_skus` row, single Default Title variant,
                    USD pricing from substrate_inventory.price, metafield
                    `retail_os.spine_sku`)
  -  5 Locations   (one per `substrate_stores` row, metafield
                    `retail_os.store_id`)
  -  N InventoryLevels per (variant × location) from substrate_store_inventory
  - 10 Draft Orders (most-recent substrate_orders, tagged
                    `retail-os-seed:<order_id>`)

Out of scope (deferred):
  - Customers / segmentation (lives in Mautic)
  - Multi-currency / Markets
  - Real (paid) orders — all seed orders land as drafts

Run after configuring `.env` (see `infra/shopify/README.md`):

    python infra/shopify/seed.py

Configuration (read from `.env` / `backend/.env`):
    SHOPIFY_SHOP_DOMAIN     (required, e.g. ai-retail-os-demo.myshopify.com)
    SHOPIFY_ADMIN_TOKEN     (required, custom-app Admin API token shpat_...)
    SHOPIFY_API_VERSION     (default 2025-01)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
SPINE_DB = ROOT / "backend" / "data" / "spine.db"


def _load_env() -> None:
    """Manual .env loader — keeps this script dependency-free."""
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

DOMAIN = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip()
TOKEN = os.environ.get("SHOPIFY_ADMIN_TOKEN", "").strip()
VERSION = os.environ.get("SHOPIFY_API_VERSION", "2025-01").strip()

if not DOMAIN or not TOKEN:
    sys.exit("SHOPIFY_SHOP_DOMAIN / SHOPIFY_ADMIN_TOKEN not set; check backend/.env")

GRAPHQL_URL = f"https://{DOMAIN}/admin/api/{VERSION}/graphql.json"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": TOKEN,
}

NS = "retail_os"
SEED_TAG = "retail-os-seed"


# ----- GraphQL helpers ------------------------------------------------------


def gql(query: str, variables: dict | None = None, retries: int = 4) -> dict:
    """POST a GraphQL operation. Retries on 429 (cost-limit) with exponential backoff."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    backoff = 1.0
    last_err: Exception | None = None
    for _ in range(retries + 1):
        req = Request(GRAPHQL_URL, data=body, headers=HEADERS, method="POST")
        try:
            with urlopen(req, timeout=30) as r:
                payload = json.loads(r.read().decode() or "{}")
            if payload.get("errors"):
                # cost-limit / throttle errors are signalled in the `errors` body too
                msg = json.dumps(payload["errors"])
                if "THROTTLED" in msg or "exceeded" in msg.lower():
                    last_err = RuntimeError(msg)
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise RuntimeError(f"GraphQL errors: {msg}")
            return payload.get("data") or {}
        except HTTPError as e:
            if e.code in (429, 502, 503):
                last_err = e
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
        except URLError as e:
            last_err = e
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"giving up after retries: {last_err}")


def _user_errors(payload: dict, op: str) -> None:
    errs = (payload or {}).get("userErrors") or []
    if errs:
        raise RuntimeError(f"{op} userErrors: {json.dumps(errs)}")


# ----- Spine readers --------------------------------------------------------


def _spine() -> sqlite3.Connection:
    if not SPINE_DB.exists():
        sys.exit(f"spine db not found at {SPINE_DB}; run `python -m app.substrate.seed` first")
    conn = sqlite3.connect(SPINE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _read_skus(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.sku, s.name, s.category, s.vendor, i.price
        FROM substrate_skus s
        LEFT JOIN substrate_inventory i ON i.sku = s.sku
        ORDER BY s.sku
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _read_stores(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM substrate_stores ORDER BY store_id").fetchall()]


def _read_store_inventory(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT store_id, sku, on_hand FROM substrate_store_inventory ORDER BY store_id, sku"
    ).fetchall()]


def _read_recent_orders(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """
        SELECT o.id, o.ts, o.sku, o.units, o.revenue, o.store_id, o.channel
        FROM substrate_orders o
        ORDER BY o.ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ----- Products -------------------------------------------------------------


PRODUCT_BY_SKU_QUERY = """
query ProductBySku($q: String!) {
  productVariants(first: 1, query: $q) {
    edges { node { id sku product { id title } } }
  }
}
"""

PRODUCT_CREATE = """
mutation ProductCreate($input: ProductInput!) {
  productCreate(input: $input) {
    product {
      id
      variants(first: 1) { edges { node { id sku } } }
    }
    userErrors { field message }
  }
}
"""

VARIANT_BULK_UPDATE = """
mutation VariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id sku price inventoryItem { id } }
    userErrors { field message }
  }
}
"""


def upsert_products(skus: list[dict]) -> dict[str, dict]:
    """Returns {spine_sku: {product_id, variant_id, inventory_item_id, created}}."""
    out: dict[str, dict] = {}
    for sku in skus:
        spine_sku = sku["sku"]
        existing = gql(PRODUCT_BY_SKU_QUERY, {"q": f"sku:{spine_sku}"})
        edges = existing.get("productVariants", {}).get("edges", [])
        if edges:
            node = edges[0]["node"]
            out[spine_sku] = {
                "product_id": node["product"]["id"],
                "variant_id": node["id"],
                "created": False,
            }
            print(f"   product {spine_sku!s:>10}  exists  {node['product']['id']}")
            continue

        # Create product with metafield, then update the auto-created Default Title variant
        title = sku["name"] or spine_sku
        price = f"{float(sku.get('price') or 0):.2f}"
        result = gql(
            PRODUCT_CREATE,
            {
                "input": {
                    "title": title,
                    "vendor": sku.get("vendor") or "AI Retail OS",
                    "productType": (sku.get("category") or "general").replace("_", " ").title(),
                    "tags": [SEED_TAG, f"category:{sku.get('category') or 'general'}"],
                    "metafields": [
                        {
                            "namespace": NS,
                            "key": "spine_sku",
                            "type": "single_line_text_field",
                            "value": spine_sku,
                        }
                    ],
                }
            },
        )
        _user_errors(result.get("productCreate"), "productCreate")
        prod = result["productCreate"]["product"]
        product_id = prod["id"]
        variant_edges = (prod.get("variants") or {}).get("edges") or []
        if not variant_edges:
            raise RuntimeError(f"productCreate returned no default variant for {spine_sku}")
        default_variant_id = variant_edges[0]["node"]["id"]

        update = gql(
            VARIANT_BULK_UPDATE,
            {
                "productId": product_id,
                "variants": [
                    {"id": default_variant_id, "price": price, "inventoryItem": {"sku": spine_sku, "tracked": True}}
                ],
            },
        )
        _user_errors(update.get("productVariantsBulkUpdate"), "productVariantsBulkUpdate")
        out[spine_sku] = {
            "product_id": product_id,
            "variant_id": default_variant_id,
            "created": True,
        }
        print(f"++ product {spine_sku!s:>10}  created {product_id}")
    return out


# ----- Locations ------------------------------------------------------------


LOCATIONS_LIST = """
query LocationsList($cursor: String) {
  locations(first: 50, after: $cursor) {
    edges {
      cursor
      node {
        id
        name
        metafield(namespace: "%s", key: "store_id") { value }
      }
    }
    pageInfo { hasNextPage }
  }
}
""" % NS

LOCATION_ADD = """
mutation LocationAdd($input: LocationAddInput!) {
  locationAdd(input: $input) {
    location { id name }
    userErrors { field message }
  }
}
"""

METAFIELDS_SET = """
mutation MetafieldsSet($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id namespace key value }
    userErrors { field message }
  }
}
"""


def _list_locations() -> list[dict]:
    out: list[dict] = []
    cursor = None
    while True:
        data = gql(LOCATIONS_LIST, {"cursor": cursor})
        conn = data.get("locations", {})
        for edge in conn.get("edges", []):
            out.append(edge["node"])
            cursor = edge.get("cursor")
        if not conn.get("pageInfo", {}).get("hasNextPage"):
            break
    return out


def upsert_locations(stores: list[dict]) -> dict[str, str]:
    """Returns {store_id: location_gid}."""
    existing = _list_locations()
    by_store: dict[str, str] = {}
    for loc in existing:
        mf = loc.get("metafield")
        if mf and mf.get("value"):
            by_store[mf["value"]] = loc["id"]

    out: dict[str, str] = {}
    for store in stores:
        store_id = store["store_id"]
        if store_id in by_store:
            out[store_id] = by_store[store_id]
            print(f"   location {store_id!s:>8}  exists  {by_store[store_id]}")
            continue
        # Shopify requires city + country code on a Location
        result = gql(
            LOCATION_ADD,
            {
                "input": {
                    "name": store.get("name") or store_id,
                    "address": {
                        "address1": "1 Demo Way",
                        "city": (store.get("region") or "Anywhere").replace("_", " ").title(),
                        "countryCode": "US",
                        "zip": "00000",
                    },
                }
            },
        )
        _user_errors(result.get("locationAdd"), "locationAdd")
        loc_id = result["locationAdd"]["location"]["id"]
        # Tag with store_id so re-runs find it without name fragility
        tag = gql(
            METAFIELDS_SET,
            {
                "metafields": [
                    {
                        "ownerId": loc_id,
                        "namespace": NS,
                        "key": "store_id",
                        "type": "single_line_text_field",
                        "value": store_id,
                    }
                ]
            },
        )
        _user_errors(tag.get("metafieldsSet"), "metafieldsSet (location)")
        out[store_id] = loc_id
        print(f"++ location {store_id!s:>8}  created {loc_id}")
    return out


# ----- Inventory levels -----------------------------------------------------


VARIANT_INVENTORY_ITEM = """
query VariantInventoryItem($id: ID!) {
  productVariant(id: $id) {
    id
    inventoryItem { id }
  }
}
"""

INVENTORY_ACTIVATE = """
mutation InventoryActivate($inventoryItemId: ID!, $locationId: ID!) {
  inventoryActivate(inventoryItemId: $inventoryItemId, locationId: $locationId) {
    inventoryLevel { id }
    userErrors { field message }
  }
}
"""

INVENTORY_SET_QTY = """
mutation InventorySetQty($input: InventorySetQuantitiesInput!) {
  inventorySetQuantities(input: $input) {
    inventoryAdjustmentGroup { reason }
    userErrors { field message }
  }
}
"""


def upsert_inventory(
    products: dict[str, dict],
    locations: dict[str, str],
    levels: list[dict],
) -> int:
    """Set on-hand for each (variant × location) pair we have substrate data for."""
    # Resolve inventory_item ids once per variant
    inv_item_by_sku: dict[str, str] = {}
    for sku, p in products.items():
        data = gql(VARIANT_INVENTORY_ITEM, {"id": p["variant_id"]})
        item = ((data or {}).get("productVariant") or {}).get("inventoryItem") or {}
        if item.get("id"):
            inv_item_by_sku[sku] = item["id"]

    set_count = 0
    for row in levels:
        sku = row["sku"]
        store_id = row["store_id"]
        on_hand = int(row.get("on_hand") or 0)
        item_id = inv_item_by_sku.get(sku)
        loc_id = locations.get(store_id)
        if not item_id or not loc_id:
            continue
        # Activate (link variant→location) — idempotent; ignore "already active" userErrors
        act = gql(INVENTORY_ACTIVATE, {"inventoryItemId": item_id, "locationId": loc_id})
        errs = ((act or {}).get("inventoryActivate") or {}).get("userErrors") or []
        for e in errs:
            msg = (e.get("message") or "").lower()
            if "already" not in msg and "exists" not in msg:
                raise RuntimeError(f"inventoryActivate userErrors: {json.dumps(errs)}")
        gql(
            INVENTORY_SET_QTY,
            {
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "ignoreCompareQuantity": True,
                    "quantities": [
                        {"inventoryItemId": item_id, "locationId": loc_id, "quantity": on_hand}
                    ],
                }
            },
        )
        set_count += 1
    print(f"== inventory levels set: {set_count}")
    return set_count


# ----- Draft orders ---------------------------------------------------------


DRAFT_ORDER_BY_TAG = """
query DraftOrderByTag($q: String!) {
  draftOrders(first: 1, query: $q) {
    edges { node { id name tags } }
  }
}
"""

DRAFT_ORDER_CREATE = """
mutation DraftOrderCreate($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder { id name }
    userErrors { field message }
  }
}
"""


def upsert_draft_orders(
    orders: list[dict],
    products: dict[str, dict],
    locations: dict[str, str],
) -> int:
    created = 0
    for order in orders:
        oid = order["id"]
        tag = f"{SEED_TAG}:{oid}"
        existing = gql(DRAFT_ORDER_BY_TAG, {"q": f"tag:{tag}"})
        edges = (existing.get("draftOrders") or {}).get("edges") or []
        if edges:
            print(f"   draft-order {oid:>4}  exists  {edges[0]['node']['name']}")
            continue
        prod = products.get(order["sku"])
        if not prod:
            continue
        line = {
            "variantId": prod["variant_id"],
            "quantity": int(order.get("units") or 1),
        }
        result = gql(
            DRAFT_ORDER_CREATE,
            {
                "input": {
                    "lineItems": [line],
                    "tags": [SEED_TAG, tag, f"channel:{order.get('channel') or 'web'}"],
                    "note": f"[retail-os:{oid}] seeded from substrate_orders",
                }
            },
        )
        _user_errors(result.get("draftOrderCreate"), "draftOrderCreate")
        name = result["draftOrderCreate"]["draftOrder"]["name"]
        created += 1
        print(f"++ draft-order {oid:>4}  created {name}")
    return created


# ----- Main -----------------------------------------------------------------


def main() -> int:
    print(f"== Shopify seed against {DOMAIN} (api {VERSION})")
    conn = _spine()
    skus = _read_skus(conn)
    stores = _read_stores(conn)
    levels = _read_store_inventory(conn)
    orders = _read_recent_orders(conn, limit=10)

    print(f"-- spine: {len(skus)} skus, {len(stores)} stores, {len(levels)} inventory rows, {len(orders)} recent orders")

    products = upsert_products(skus)
    locations = upsert_locations(stores)
    upsert_inventory(products, locations, levels)
    upsert_draft_orders(orders, products, locations)

    print("== done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
