"""Idempotent demo seed for OpenBoxes.

Projects spine.db substrate into a running OpenBoxes instance via the
REST API. Stdlib-only.

What gets created (against an empty OpenBoxes):
  - 5 Locations  (one per `substrate_stores` row)
  - 30 Products  (one per `substrate_skus` × `substrate_inventory` row)

Out of scope (deferred to P3+):
  - Stock-on-hand levels per (Product × Location) — the WAR exposes
    these but the round-trip touches a half-dozen domain endpoints
    (StockMovement / StockTransfer); easier to import via the seeded
    sample-data CSVs once we know the operator's exact OpenBoxes minor.
  - Inbound shipments / Purchase Orders — substrate models them, but
    OpenBoxes' shipment lifecycle has stricter validation than we want
    in a one-shot seed.

Run after `make openboxes-bootstrap`:

    python infra/openboxes/seed.py

Configuration (read from `.env` / `backend/.env`):
    OPENBOXES_BASE_URL          (default http://localhost:8082)
    OPENBOXES_USERNAME          (required unless OPENBOXES_API_TOKEN)
    OPENBOXES_PASSWORD          (required unless OPENBOXES_API_TOKEN)
    OPENBOXES_API_TOKEN         (optional — bypass login flow)
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

BASE = os.environ.get("OPENBOXES_BASE_URL", "http://localhost:8082").rstrip("/")
USER = os.environ.get("OPENBOXES_USERNAME")
PASSWORD = os.environ.get("OPENBOXES_PASSWORD")
TOKEN = os.environ.get("OPENBOXES_API_TOKEN")

if not TOKEN and not (USER and PASSWORD):
    sys.exit(
        "OPENBOXES_API_TOKEN or OPENBOXES_USERNAME + OPENBOXES_PASSWORD "
        "must be set; check backend/.env"
    )


# ----- REST helpers ---------------------------------------------------------


_AUTH_TOKEN: str | None = TOKEN


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
        if _AUTH_TOKEN is None:
            raise RuntimeError("called auth-required endpoint before login")
        headers["X-Auth-Token"] = _AUTH_TOKEN
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
    """`POST /api/login` → {token}.

    OpenBoxes 0.9 supports session cookies *and* a token-based path; we
    use the token path because it round-trips cleanly through the
    cockpit's adapter without juggling cookie jars.
    """
    global _AUTH_TOKEN
    if _AUTH_TOKEN:
        return  # operator-supplied token wins
    status, body = _request(
        "POST",
        "/api/login",
        {"username": USER, "password": PASSWORD},
        auth=False,
    )
    if status != 200:
        sys.exit(f"openboxes login failed [{status}]: {body}")
    token = (body.get("data") or body).get("token") or body.get("token")
    if not token:
        sys.exit(f"openboxes login returned no token: {body}")
    _AUTH_TOKEN = token


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-") or "item"


def _list(endpoint: str, key: str, query: dict | None = None) -> list[dict]:
    """GET /api/<endpoint> — single page is fine for demo scale."""
    qs = urlencode(query or {})
    path = f"/api/{endpoint}" + (f"?{qs}" if qs else "")
    status, body = _request("GET", path)
    if status != 200:
        raise RuntimeError(f"GET /api/{endpoint} failed [{status}]: {body}")
    rows = body.get(key) or body.get("data") or []
    if isinstance(rows, dict):
        return list(rows.values())
    return rows if isinstance(rows, list) else []


# ----- Seed steps -----------------------------------------------------------


def ensure_locations(spine: sqlite3.Connection) -> dict[str, str]:
    """One Depot location per substrate store. Returns store_id → ob id."""
    rows = spine.execute(
        "SELECT store_id, name, region FROM substrate_stores"
    ).fetchall()
    existing = _list("locations", "data")
    by_name = {loc.get("name"): loc for loc in existing}
    out: dict[str, str] = {}
    for store_id, name, region in rows:
        if name in by_name:
            out[store_id] = by_name[name]["id"]
            print(f"   Location '{name}' already present (id={out[store_id]})")
            continue
        status, body = _request(
            "POST",
            "/api/locations",
            {
                "name": name,
                "active": True,
                # OpenBoxes' Location domain has a free-form `description`
                # field — we stash the substrate id there for the P3
                # round-trip (mirror of metadata.retail_os_store_id on
                # Medusa). The cockpit's adapter parses it back out.
                "description": f"[retail-os:{store_id}] region={region}",
                "locationType": {"name": "Depot"},
            },
        )
        if status not in (200, 201):
            raise RuntimeError(f"create location {name!r} failed [{status}]: {body}")
        loc = (body.get("data") or body) or {}
        out[store_id] = loc["id"]
        print(f"++ Location '{name}' (id={loc['id']})")
    return out


def ensure_products(spine: sqlite3.Connection) -> None:
    """One Product per substrate SKU. Idempotent by productCode == SKU."""
    rows = spine.execute(
        "SELECT s.sku, s.name, s.category, s.vendor, i.price "
        "FROM substrate_skus s "
        "JOIN substrate_inventory i ON i.sku = s.sku "
        "ORDER BY s.sku"
    ).fetchall()
    for sku, name, category, vendor, price in rows:
        # Most OpenBoxes deployments accept productCode as the
        # idempotency key. We GET-by-productCode, fall back to a name
        # search if that endpoint isn't supported.
        status, body = _request("GET", f"/api/products?productCode={sku}")
        if status == 200:
            existing = body.get("data") or []
            if isinstance(existing, list) and existing:
                print(f"   Product {sku} already present")
                continue
        post_status, post_body = _request(
            "POST",
            "/api/products",
            {
                "name": name,
                "productCode": sku,
                "category": {"name": category.replace("_", " ").title()},
                "manufacturer": vendor,
                "pricePerUnit": float(price),
                "description": f"[retail-os:{sku}] {category} via AI Retail OS seed",
                "active": True,
            },
        )
        if post_status not in (200, 201):
            raise RuntimeError(
                f"create product {sku!r} failed [{post_status}]: {post_body}"
            )
        prod = post_body.get("data") or post_body or {}
        print(f"++ Product {sku} (id={prod.get('id', '?')})")


# ----- Driver ---------------------------------------------------------------


def main() -> None:
    if not SPINE_DB.exists():
        sys.exit(
            f"spine.db not found at {SPINE_DB} — run "
            "`python -m app.substrate.seed` from backend/ first."
        )
    print(f">> connecting to OpenBoxes at {BASE}")
    login()
    spine = sqlite3.connect(SPINE_DB)
    try:
        ensure_locations(spine)
        ensure_products(spine)
    finally:
        spine.close()
    print(">> openboxes seed complete")


if __name__ == "__main__":
    main()
