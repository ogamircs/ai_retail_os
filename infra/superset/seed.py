"""Idempotent demo seed for Apache Superset.

Registers the cockpit's spine.db SQLite as a SQLAlchemy database
connection inside Superset, then creates a small set of datasets +
charts + a dashboard that the cockpit's Analyst can deep-link into.

Stdlib-only. Auth via Superset's Flask-AppBuilder login + CSRF flow.

What gets created:
  - 1 Database connection: 'AI Retail OS spine'
        URI: sqlite:////spine/spine.db
  - 3 Datasets over substrate tables: substrate_skus, substrate_orders,
        substrate_inventory
  - 3 Charts: top SKUs by units, orders by channel, inventory by store
  - 1 Dashboard: 'AI Retail OS — Demo'

Idempotency: each entity is looked up by stable name first; we POST
only when no match is found. Re-runs print zero `++` lines.

Out of scope (deferred):
  - Slice ownership / row-level security (not needed for the demo).
  - Custom charts beyond the three default ones.
  - Auto-refresh / cache rules — Superset defaults are fine.

Run after `make superset-bootstrap`:

    python infra/superset/seed.py

Configuration (read from `.env` / `backend/.env`):
    SUPERSET_BASE_URL       (default http://localhost:8088)
    SUPERSET_USERNAME       (required)
    SUPERSET_PASSWORD       (required)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]


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

BASE = os.environ.get("SUPERSET_BASE_URL", "http://localhost:8088").rstrip("/")
USER = os.environ.get("SUPERSET_USERNAME")
PASSWORD = os.environ.get("SUPERSET_PASSWORD")

if not all((USER, PASSWORD)):
    sys.exit(
        "SUPERSET_USERNAME / SUPERSET_PASSWORD must be set; check backend/.env"
    )


_TOKEN: str | None = None
_CSRF: str | None = None


def _request(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    auth: bool = True,
    timeout: int = 20,
) -> dict:
    headers = {"Accept": "application/json"}
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth:
        if _TOKEN is None:
            _login()
        headers["Authorization"] = f"Bearer {_TOKEN}"
        if _CSRF and method != "GET":
            headers["X-CSRFToken"] = _CSRF
    url = path if path.startswith("http") else BASE + path
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text) if text else {}
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} → {e.code} {detail}") from None
    except URLError as e:
        raise RuntimeError(f"{method} {path} → connection error: {e}") from None


def _login() -> None:
    global _TOKEN, _CSRF
    data = _request(
        "POST",
        "/api/v1/security/login",
        {
            "username": USER,
            "password": PASSWORD,
            "provider": "db",
            "refresh": True,
        },
        auth=False,
    )
    _TOKEN = data.get("access_token")
    if not _TOKEN:
        raise RuntimeError(f"login returned no access_token: {data}")
    csrf = _request("GET", "/api/v1/security/csrf_token/")
    _CSRF = csrf.get("result")


# ---------------------------------------------------------------------------
# domain helpers
# ---------------------------------------------------------------------------

def _q_filter(filters: list[dict]) -> str:
    """Build the Rison-ish ?q= filter Superset's list endpoints expect.

    Superset wants a Rison-encoded payload; for the simple equality
    filters we use, a hand-crafted string is enough and avoids the
    `prison` dependency.
    """
    parts = []
    for f in filters:
        col = f["col"]
        opr = f["opr"]
        val = f["value"]
        if isinstance(val, str):
            val = f"'{val}'"
        parts.append(f"(col:{col},opr:{opr},value:{val})")
    return "q=(filters:!(" + ",".join(parts) + "))"


def _list(endpoint: str, filters: list[dict]) -> list[dict]:
    qs = _q_filter(filters)
    data = _request("GET", f"{endpoint}?{qs}")
    return data.get("result") or []


def _ensure_database() -> int:
    name = "AI Retail OS spine"
    found = _list("/api/v1/database/", [{"col": "database_name", "opr": "eq", "value": name}])
    if found:
        print(f"   database '{name}' already exists (id={found[0]['id']})")
        return int(found[0]["id"])
    body = {
        "database_name": name,
        "engine": "sqlite",
        "sqlalchemy_uri": "sqlite:////spine/spine.db",
        "expose_in_sqllab": True,
        "allow_run_async": False,
    }
    out = _request("POST", "/api/v1/database/", body)
    db_id = int(out["id"])
    print(f"++ created database '{name}' (id={db_id})")
    return db_id


def _ensure_dataset(database_id: int, table_name: str) -> int:
    found = _list(
        "/api/v1/dataset/",
        [
            {"col": "table_name", "opr": "eq", "value": table_name},
            {"col": "database", "opr": "rel_o_m", "value": database_id},
        ],
    )
    if found:
        print(f"   dataset '{table_name}' already exists (id={found[0]['id']})")
        return int(found[0]["id"])
    body = {
        "database": database_id,
        "schema": "main",
        "table_name": table_name,
    }
    out = _request("POST", "/api/v1/dataset/", body)
    ds_id = int(out["id"])
    print(f"++ created dataset '{table_name}' (id={ds_id})")
    return ds_id


def _ensure_chart(slice_name: str, dataset_id: int, viz_type: str, params: dict) -> int:
    found = _list("/api/v1/chart/", [{"col": "slice_name", "opr": "eq", "value": slice_name}])
    if found:
        print(f"   chart '{slice_name}' already exists (id={found[0]['id']})")
        return int(found[0]["id"])
    body = {
        "slice_name": slice_name,
        "datasource_id": dataset_id,
        "datasource_type": "table",
        "viz_type": viz_type,
        "params": json.dumps(params),
    }
    out = _request("POST", "/api/v1/chart/", body)
    cid = int(out["id"])
    print(f"++ created chart '{slice_name}' (id={cid})")
    return cid


def _ensure_dashboard(title: str, chart_ids: list[int]) -> int:
    found = _list("/api/v1/dashboard/", [{"col": "dashboard_title", "opr": "eq", "value": title}])
    if found:
        print(f"   dashboard '{title}' already exists (id={found[0]['id']})")
        return int(found[0]["id"])
    body = {
        "dashboard_title": title,
        "slug": "ai-retail-os-demo",
        "published": True,
    }
    out = _request("POST", "/api/v1/dashboard/", body)
    dash_id = int(out["id"])
    # Wire charts into the dashboard via the bulk-add endpoint shape.
    for cid in chart_ids:
        try:
            _request("PUT", f"/api/v1/chart/{cid}", {"dashboards": [dash_id]})
        except Exception as e:
            # Non-fatal: the chart still exists; the operator can drop
            # it onto the dashboard manually if the v1 API shape drifts.
            print(f"   (couldn't auto-link chart {cid} to dashboard {dash_id}: {e})")
    print(f"++ created dashboard '{title}' (id={dash_id})")
    return dash_id


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    db_id = _ensure_database()

    sku_ds = _ensure_dataset(db_id, "substrate_skus")
    order_ds = _ensure_dataset(db_id, "substrate_orders")
    inventory_ds = _ensure_dataset(db_id, "substrate_inventory")

    chart_top_skus = _ensure_chart(
        "Retail · Top SKUs",
        sku_ds,
        "table",
        {
            "viz_type": "table",
            "all_columns": ["sku", "title", "category", "vendor", "price"],
            "row_limit": 30,
        },
    )
    chart_orders_by_channel = _ensure_chart(
        "Retail · Orders by channel",
        order_ds,
        "pie",
        {
            "viz_type": "pie",
            "groupby": ["channel"],
            "metrics": [{"label": "count", "expressionType": "SQL", "sqlExpression": "COUNT(1)"}],
        },
    )
    chart_inventory = _ensure_chart(
        "Retail · Inventory by store",
        inventory_ds,
        "dist_bar",
        {
            "viz_type": "dist_bar",
            "groupby": ["store_id"],
            "metrics": [
                {
                    "label": "on_hand",
                    "expressionType": "SQL",
                    "sqlExpression": "SUM(on_hand)",
                }
            ],
        },
    )

    _ensure_dashboard(
        "AI Retail OS — Demo",
        [chart_top_skus, chart_orders_by_channel, chart_inventory],
    )


if __name__ == "__main__":
    main()
