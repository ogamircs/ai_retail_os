"""Idempotent demo seed for Medusa v2.

Projects the canonical retail demo data from `backend/data/spine.db` into a
running Medusa instance via the v2 admin REST API. Safe to re-run — every
write checks for existing rows first.

What gets created (against an empty Medusa):
  - 1  Sales Channel  ("Retail Demo")
  - 5  Stock Locations (one per `substrate_stores` row)
  - 30 Products       (one per `substrate_skus` row, single variant, USD pricing)

Out of scope (P3+):
  - Inventory levels per (variant × stock_location) — requires linking
    inventory_items to stock_locations after they're both created.
  - Orders / customers / regions — Medusa v2 makes these region+cart-bound
    so a meaningful order seed is more involved than a flat row.

Run after `make medusa-bootstrap`:

    python infra/medusa/seed.py

Configuration (read from `.env` / `backend/.env`):
    MEDUSA_BASE_URL          (default http://localhost:9000)
    MEDUSA_ADMIN_EMAIL       (required)
    MEDUSA_ADMIN_PASSWORD    (required)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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

BASE = os.environ.get("MEDUSA_BASE_URL", "http://localhost:9000").rstrip("/")
EMAIL = os.environ.get("MEDUSA_ADMIN_EMAIL")
PASSWORD = os.environ.get("MEDUSA_ADMIN_PASSWORD")

if not EMAIL or not PASSWORD:
    sys.exit("MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD not set; check backend/.env")


# ----- Medusa REST helpers --------------------------------------------------


_TOKEN: str | None = None


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    retries: int = 2,
    *,
    auth: bool = True,
) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if auth:
        if _TOKEN is None:
            raise RuntimeError("called auth-required endpoint before login")
        headers["Authorization"] = f"Bearer {_TOKEN}"
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


def login() -> None:
    """Medusa v2 admin auth: POST /auth/user/emailpass → {token}.

    The returned token is a JWT we send as Bearer for /admin/*.
    """
    global _TOKEN
    status, body = _request(
        "POST",
        "/auth/user/emailpass",
        {"email": EMAIL, "password": PASSWORD},
        auth=False,
    )
    if status != 200:
        sys.exit(f"medusa login failed [{status}]: {body}")
    token = body.get("token")
    if not token:
        sys.exit(f"medusa login returned no token: {body}")
    _TOKEN = token


def _list_admin(endpoint: str, key: str, query: dict | None = None) -> list[dict]:
    """GET /admin/<endpoint> with paginated walk until exhausted.

    Mirrors the Mautic seed pattern — never silently truncate.
    """
    out: list[dict] = []
    offset = 0
    limit = 100
    while True:
        params = {"limit": limit, "offset": offset, **(query or {})}
        qs = urlencode(params)
        status, body = _request("GET", f"/admin/{endpoint}?{qs}")
        if status != 200:
            raise RuntimeError(f"GET /admin/{endpoint} failed [{status}]: {body}")
        rows = body.get(key) or []
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        count = int(body.get("count") or 0)
        if count and offset + len(rows) >= count:
            break
        if len(rows) < limit:
            break
        offset += len(rows)
    return out


def _slug(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "item"


# ----- Seed steps -----------------------------------------------------------


CHANNEL_NAME = "Retail Demo"


def ensure_sales_channel() -> str:
    """Create / find the demo sales channel. Returns its id."""
    channels = _list_admin("sales-channels", "sales_channels")
    for ch in channels:
        if ch.get("name") == CHANNEL_NAME:
            print(f"   Sales channel '{CHANNEL_NAME}' already present (id={ch['id']})")
            return ch["id"]
    status, body = _request(
        "POST",
        "/admin/sales-channels",
        {"name": CHANNEL_NAME, "description": "AI Retail OS demo storefront."},
    )
    if status not in (200, 201):
        raise RuntimeError(f"create sales channel failed [{status}]: {body}")
    sc = body.get("sales_channel") or {}
    print(f"++ Sales channel '{CHANNEL_NAME}' (id={sc.get('id')})")
    return sc["id"]


def ensure_stock_locations(spine: sqlite3.Connection) -> dict[str, str]:
    """One stock location per substrate store. Returns store_id → medusa id."""
    rows = spine.execute(
        "SELECT store_id, name, region FROM substrate_stores"
    ).fetchall()
    existing = _list_admin("stock-locations", "stock_locations")
    by_name = {loc.get("name"): loc for loc in existing}
    out: dict[str, str] = {}
    for store_id, name, region in rows:
        if name in by_name:
            out[store_id] = by_name[name]["id"]
            print(f"   Stock location '{name}' already present (id={out[store_id]})")
            continue
        status, body = _request(
            "POST",
            "/admin/stock-locations",
            {
                "name": name,
                "metadata": {
                    "retail_os_store_id": store_id,
                    "region": region,
                },
            },
        )
        if status not in (200, 201):
            raise RuntimeError(f"create stock location {name!r} failed [{status}]: {body}")
        loc = body.get("stock_location") or {}
        out[store_id] = loc["id"]
        print(f"++ Stock location '{name}' (id={loc['id']})")
    return out


def ensure_products(spine: sqlite3.Connection, sales_channel_id: str) -> None:
    """One product per substrate SKU, single variant, USD pricing.

    Idempotency: handle == slugified SKU. The /admin/products endpoint
    accepts `?handle=…` so we don't need to walk the whole catalogue
    when seeding hundreds of SKUs.
    """
    rows = spine.execute(
        "SELECT s.sku, s.name, s.category, s.vendor, i.price, i.base_price "
        "FROM substrate_skus s "
        "JOIN substrate_inventory i ON i.sku = s.sku "
        "ORDER BY s.sku"
    ).fetchall()
    for sku, name, category, vendor, price, _base_price in rows:
        handle = _slug(sku)
        status, body = _request("GET", f"/admin/products?handle={handle}&limit=1")
        if status != 200:
            raise RuntimeError(f"GET /admin/products?handle={handle} failed [{status}]: {body}")
        existing = (body.get("products") or [])
        if existing:
            print(f"   Product {sku} ({handle}) already present")
            continue
        # Medusa v2 prices live on a price-set linked to the variant; the
        # /admin/products POST accepts an inline `prices` array on each
        # variant for the convenience case (USD only — we're not seeding
        # multi-currency for the demo).
        product_payload = {
            "title": name,
            "handle": handle,
            "status": "published",
            "description": f"{vendor} · {category.replace('_', ' ').title()}",
            "metadata": {
                "retail_os_sku": sku,
                "retail_os_category": category,
                "retail_os_vendor": vendor,
            },
            "options": [{"title": "Default", "values": ["Default"]}],
            "variants": [
                {
                    "title": "Default",
                    "sku": sku,
                    "manage_inventory": True,
                    "options": {"Default": "Default"},
                    "prices": [
                        {"currency_code": "usd", "amount": int(round(float(price) * 100))},
                    ],
                }
            ],
            "sales_channels": [{"id": sales_channel_id}],
        }
        post_status, post_body = _request("POST", "/admin/products", product_payload)
        if post_status not in (200, 201):
            raise RuntimeError(
                f"create product {sku!r} failed [{post_status}]: {post_body}"
            )
        product = post_body.get("product") or {}
        print(f"++ Product {sku} ({handle}) (id={product.get('id')})")


# ----- Driver ---------------------------------------------------------------


def main() -> None:
    if not SPINE_DB.exists():
        sys.exit(
            f"spine.db not found at {SPINE_DB} — run "
            "`python -m app.substrate.seed` from backend/ first."
        )
    print(f">> connecting to Medusa at {BASE}")
    login()
    spine = sqlite3.connect(SPINE_DB)
    try:
        sales_channel_id = ensure_sales_channel()
        ensure_stock_locations(spine)
        ensure_products(spine, sales_channel_id)
    finally:
        spine.close()
    print(">> medusa seed complete")


if __name__ == "__main__":
    main()
