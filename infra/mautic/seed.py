"""Idempotent demo seed for Mautic.

Projects the canonical retail demo data from `backend/data/spine.db` into a
running Mautic instance via the Mautic 5 REST API. Safe to re-run — every
write checks for existing rows first.

What gets created:
  - 4 Segments  (one per `substrate_customer_segments` row)
  - 20 Contacts (5 synthetic contacts per segment, tagged with segment + tier)
  - N Campaigns (one *draft* per row in `substrate_campaigns`)

Run after `make mautic-bootstrap`:

    python infra/mautic/seed.py

Configuration is read from `.env` / `backend/.env`:
    MAUTIC_BASE_URL  (default http://localhost:8081)
    MAUTIC_USERNAME  (required — basic-auth user, usually admin)
    MAUTIC_PASSWORD  (required — basic-auth pw)

Mautic auth: basic auth on /api/* (toggled on by infra/mautic/bootstrap.sh).
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
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

BASE = os.environ.get("MAUTIC_BASE_URL", "http://localhost:8081").rstrip("/")
USER = os.environ.get("MAUTIC_USERNAME")
PASSWORD = os.environ.get("MAUTIC_PASSWORD")

if not USER or not PASSWORD:
    sys.exit("MAUTIC_USERNAME / MAUTIC_PASSWORD not set; check backend/.env")


# ----- Mautic REST helpers --------------------------------------------------

_BASIC = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()


def _request(method: str, path: str, body: dict | None = None, retries: int = 2):
    headers = {
        "Authorization": f"Basic {_BASIC}",
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


def search_one(endpoint: str, search: str, key: str) -> dict | None:
    """Mautic search syntax: `?search=email:foo` / `?search=alias:bar`.
    Returns the first matching row from the keyed dict response, or None.
    """
    qs = urlencode({"search": search, "limit": 1})
    status, body = _request("GET", f"/api/{endpoint}?{qs}")
    if status != 200:
        return None
    rows = body.get(key) or {}
    if isinstance(rows, dict) and rows:
        # Mautic returns rows keyed by id: {"42": {...}}
        return next(iter(rows.values()))
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def post_new(endpoint: str, payload: dict, key: str) -> dict:
    status, body = _request("POST", f"/api/{endpoint}/new", payload)
    if status not in (200, 201):
        raise RuntimeError(f"create {endpoint} failed [{status}]: {body}")
    return body.get(key) or {}


# ----- Seed steps -----------------------------------------------------------

# 5 synthetic contacts per segment — enough to demo lists + push without
# polluting Mautic's UI for the operator. Names are stable so re-runs are
# fully idempotent (search-by-email matches the same row).
CONTACTS_PER_SEGMENT = 5
PERSONAS = [
    ("Jordan", "Reyes"),
    ("Avery", "Patel"),
    ("Morgan", "Kim"),
    ("Riley", "Nguyen"),
    ("Casey", "Okafor"),
]


def ensure_segments(spine: sqlite3.Connection) -> dict[str, int]:
    """Create one Mautic Segment per substrate segment. Returns segment_id → mautic_id."""
    rows = spine.execute(
        "SELECT segment_id, name, loyalty_tier, channel_preference, notes "
        "FROM substrate_customer_segments"
    ).fetchall()
    out: dict[str, int] = {}
    for seg_id, name, tier, channel, notes in rows:
        alias = seg_id.replace("-", "_")  # Mautic alias must be alnum + underscore
        existing = search_one("segments", f"alias:{alias}", "lists")
        if existing:
            print(f"   Segment {alias} already present (id={existing['id']})")
            out[seg_id] = int(existing["id"])
            continue
        created = post_new(
            "segments",
            {
                "name": name,
                "alias": alias,
                "publicName": name,
                "description": f"{tier} · {channel}. {notes}",
                "isPublished": True,
                "isGlobal": True,
            },
            "list",
        )
        sid = int(created.get("id"))
        out[seg_id] = sid
        print(f"++ Segment {alias} (id={sid})")
    return out


def ensure_contacts(seg_map: dict[str, int], spine: sqlite3.Connection) -> None:
    """Create CONTACTS_PER_SEGMENT synthetic contacts per segment, tagged."""
    seg_meta = {
        row[0]: {"name": row[1], "tier": row[2]}
        for row in spine.execute(
            "SELECT segment_id, name, loyalty_tier FROM substrate_customer_segments"
        )
    }
    for seg_id in seg_map:
        meta = seg_meta[seg_id]
        for first, last in PERSONAS[:CONTACTS_PER_SEGMENT]:
            email = f"{first.lower()}.{last.lower()}.{seg_id}@retail.local"
            existing = search_one("contacts", f"email:{email}", "contacts")
            if existing:
                continue
            post_new(
                "contacts",
                {
                    "firstname": first,
                    "lastname": last,
                    "email": email,
                    "tags": [seg_id, f"tier-{meta['tier']}", "retail-os-seed"],
                },
                "contact",
            )
            print(f"++ Contact {email}")


def ensure_campaigns(spine: sqlite3.Connection) -> None:
    """Create one *draft* Mautic Campaign per substrate row.

    Mautic campaigns require events/actions to be functional; we deliberately
    leave them unpublished so the operator (or P4 outbound apply) can fill in
    the workflow later. The cockpit's Marketing agent reads these as canvases,
    not as live automations.
    """
    rows = spine.execute(
        "SELECT campaign_id, title, category, segment_id, channel, budget, offer, "
        "       projected_lift, projected_roi, starts_at, ends_at "
        "FROM substrate_campaigns ORDER BY starts_at"
    ).fetchall()
    for (
        cmp_id,
        title,
        category,
        segment_id,
        channel,
        budget,
        offer,
        proj_lift,
        proj_roi,
        starts_at,
        _ends_at,
    ) in rows:
        # Mautic campaign names aren't unique in the DB; we use the spine
        # campaign_id as a tag in the description so re-runs find the existing.
        marker = f"[retail-os:{cmp_id}]"
        existing = search_one("campaigns", marker, "campaigns")
        if existing:
            print(f"   Campaign {cmp_id} already present (id={existing['id']})")
            continue
        post_new(
            "campaigns",
            {
                "name": title,
                "description": (
                    f"{marker}\n"
                    f"Category: {category} · Segment: {segment_id} · Channel: {channel}\n"
                    f"Offer: {offer}\n"
                    f"Projected lift: {proj_lift:.0%} · Projected ROI: {proj_roi:.2f}\n"
                    f"Budget: ${budget:,.0f} · Starts: {starts_at}"
                ),
                "isPublished": False,  # draft — needs actions before going live
            },
            "campaign",
        )
        print(f"++ Campaign {cmp_id} ({title})")


# ----- Driver ---------------------------------------------------------------

def main() -> None:
    if not SPINE_DB.exists():
        sys.exit(
            f"spine.db not found at {SPINE_DB} — run "
            "`python -m app.substrate.seed` from backend/ first."
        )
    print(f">> connecting to Mautic at {BASE}")
    # Sanity ping so we fail fast on bad creds / Mautic down.
    status, _ = _request("GET", "/api/contacts?limit=1")
    if status == 401:
        sys.exit("auth failed — re-run `make mautic-bootstrap` to re-toggle basic-auth")
    if status >= 400:
        sys.exit(f"Mautic /api/contacts returned {status} — is the stack up?")

    spine = sqlite3.connect(SPINE_DB)
    try:
        seg_map = ensure_segments(spine)
        ensure_contacts(seg_map, spine)
        ensure_campaigns(spine)
    finally:
        spine.close()
    print(">> mautic seed complete")


if __name__ == "__main__":
    main()
