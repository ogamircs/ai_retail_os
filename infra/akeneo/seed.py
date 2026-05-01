"""Idempotent demo seed for Akeneo PIM Community Edition.

Projects spine.db substrate into a running Akeneo instance via the
v1 REST API. Stdlib-only.

What gets created (against the minimal-catalog Akeneo install):
  - 3 Categories (one per `substrate_categories`, parent=master)
  - 30 Products (one per `substrate_skus` × `substrate_inventory`)

Out of scope (deferred to P3+):
  - Families / Attributes / AttributeOptions — Akeneo's CE default
    catalog already ships a `default` family that's good enough for
    a demo product walkthrough.
  - Asset / media uploads.
  - Multi-locale enrichment.

Run after `make akeneo-bootstrap`:

    python infra/akeneo/seed.py

Configuration (read from `.env` / `backend/.env`):
    AKENEO_BASE_URL         (default http://localhost:8083)
    AKENEO_CLIENT_ID        (required — OAuth2 client)
    AKENEO_SECRET           (required — OAuth2 client secret)
    AKENEO_USERNAME         (required — admin user)
    AKENEO_PASSWORD         (required — admin password)
"""

from __future__ import annotations

import base64
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

BASE = os.environ.get("AKENEO_BASE_URL", "http://localhost:8083").rstrip("/")
CLIENT_ID = os.environ.get("AKENEO_CLIENT_ID")
SECRET = os.environ.get("AKENEO_SECRET")
USER = os.environ.get("AKENEO_USERNAME")
PASSWORD = os.environ.get("AKENEO_PASSWORD")

if not all((CLIENT_ID, SECRET, USER, PASSWORD)):
    sys.exit(
        "AKENEO_CLIENT_ID / AKENEO_SECRET / AKENEO_USERNAME / AKENEO_PASSWORD "
        "must be set; check backend/.env"
    )


_TOKEN: str | None = None


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    auth: bool = True,
    form: bool = False,
    extra_headers: dict | None = None,
) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    if auth:
        if _TOKEN is None:
            raise RuntimeError("called auth-required endpoint before login")
        headers["Authorization"] = f"Bearer {_TOKEN}"
    data = None
    if body is not None:
        if form:
            data = urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    last_err: Exception | None = None
    for attempt in range(3):
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
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise
    raise last_err  # type: ignore[misc]


def login() -> None:
    """`POST /api/oauth/v1/token` (password grant) → bearer token."""
    global _TOKEN
    basic = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    status, body = _request(
        "POST",
        "/api/oauth/v1/token",
        {
            "grant_type": "password",
            "username": USER,
            "password": PASSWORD,
        },
        auth=False,
        form=True,
        extra_headers={"Authorization": f"Basic {basic}"},
    )
    if status != 200:
        sys.exit(f"akeneo login failed [{status}]: {body}")
    token = body.get("access_token")
    if not token:
        sys.exit(f"akeneo login returned no access_token: {body}")
    _TOKEN = token


def _get_one(endpoint: str, code: str) -> dict | None:
    """Akeneo CE supports GET /api/rest/v1/<resource>/<code>. 404 if missing."""
    status, body = _request("GET", f"/api/rest/v1/{endpoint}/{code}")
    return body if status == 200 else None


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    return text or "item"


# ----- Seed steps -----------------------------------------------------------


def ensure_categories(spine: sqlite3.Connection) -> None:
    rows = spine.execute(
        "SELECT category, display_name FROM substrate_categories"
    ).fetchall()
    for category, display in rows:
        code = _slug(category)
        if _get_one("categories", code):
            print(f"   Category {code} already present")
            continue
        status, body = _request(
            "POST",
            "/api/rest/v1/categories",
            {
                "code": code,
                "parent": "master",
                "labels": {"en_US": display},
            },
        )
        if status not in (200, 201):
            raise RuntimeError(f"create category {code!r} failed [{status}]: {body}")
        print(f"++ Category {code} ({display})")


def ensure_products(spine: sqlite3.Connection) -> None:
    """Akeneo POST /api/rest/v1/products — uses `identifier == sku`.

    `family` defaults to `default` (Akeneo CE's bundled minimal family).
    Pricing goes into the catalog-default `price` attribute when present;
    we skip it here because the minimal catalog doesn't ship one and
    we'd need to create the attribute first. The cockpit's adapter
    reads price from substrate, not from Akeneo, so this is fine.
    """
    rows = spine.execute(
        "SELECT s.sku, s.name, s.category, s.vendor "
        "FROM substrate_skus s "
        "ORDER BY s.sku"
    ).fetchall()
    for sku, name, category, vendor in rows:
        if _get_one("products", sku):
            print(f"   Product {sku} already present")
            continue
        status, body = _request(
            "POST",
            "/api/rest/v1/products",
            {
                "identifier": sku,
                "family": "default",
                "enabled": True,
                "categories": [_slug(category)],
                "values": {
                    "name": [{"locale": "en_US", "scope": None, "data": name}],
                    # Stash retail-os ids in description so the P3 sync
                    # can round-trip them (mirror of the
                    # [retail-os:<id>] marker we use elsewhere).
                    "description": [
                        {
                            "locale": "en_US",
                            "scope": None,
                            "data": f"[retail-os:{sku}] {vendor} · {category}",
                        }
                    ],
                },
            },
        )
        if status not in (200, 201, 204):
            raise RuntimeError(f"create product {sku!r} failed [{status}]: {body}")
        print(f"++ Product {sku} ({name})")


# ----- Driver ---------------------------------------------------------------


def main() -> None:
    if not SPINE_DB.exists():
        sys.exit(
            f"spine.db not found at {SPINE_DB} — run "
            "`python -m app.substrate.seed` from backend/ first."
        )
    print(f">> connecting to Akeneo at {BASE}")
    login()
    spine = sqlite3.connect(SPINE_DB)
    try:
        ensure_categories(spine)
        ensure_products(spine)
    finally:
        spine.close()
    print(">> akeneo seed complete")


if __name__ == "__main__":
    main()
