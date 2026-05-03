from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timezone
import json
import os
import re
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from app.integrations.base import IntegrationAdapter, IntegrationDefinition, IntegrationResult
from app.integrations import store
from app.spine.db import conn


def _slug(value: str | None, default: str = "uncategorized") -> str:
    text = (value or default).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or default


def _display(value: str) -> str:
    return value.replace("_", " ").title()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


class JsonHttpClient:
    def __init__(self, base_url: str, headers: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}

    def request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self.headers,
        }
        req = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        with urlopen(req, timeout=12) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data) if data else {}


class ERPNextAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="erpnext",
        display_name="ERPNext",
        domain="ERP / POS / Inventory",
        docs_url="https://docs.frappe.io/framework/user/en/api/rest",
        env_keys=("ERPNEXT_BASE_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET"),
        notes="First system of record for POS invoices, stock, vendors, purchase orders, and pricing drafts.",
    )

    def _client(self) -> JsonHttpClient:
        token = f"{os.environ['ERPNEXT_API_KEY']}:{os.environ['ERPNEXT_API_SECRET']}"
        return JsonHttpClient(
            os.environ["ERPNEXT_BASE_URL"],
            headers={"Authorization": f"token {token}"},
        )

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:
            data = self._client().request("/api/method/frappe.auth.get_logged_user")
            return IntegrationResult(status="connected", summary={"logged_user": data.get("message")})
        except Exception as exc:  # pragma: no cover - exercised only with live ERPNext
            return IntegrationResult(status="error", error=str(exc))

    def sync_inbound(self) -> IntegrationResult:
        if not self.configured():
            return self._mock_sync()
        try:
            return self._live_sync()
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    def _frappe_list(
        self,
        doctype: str,
        fields: list[str],
        filters: list[list[Any]] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params = {
            "fields": json.dumps(fields),
            "limit_page_length": str(limit),
        }
        if filters:
            params["filters"] = json.dumps(filters)
        data = self._client().request(f"/api/resource/{quote(doctype)}?{urlencode(params)}")
        return data.get("data", [])

    def _live_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_read = 0
        records_written = 0

        item_groups = self._frappe_list("Item Group", ["name", "item_group_name"], limit=200)
        records_read += len(item_groups)
        for group in item_groups:
            category = _slug(group.get("item_group_name") or group.get("name"))
            self._upsert_category(category, group.get("item_group_name") or group.get("name") or category)
            self._cache("Item Group", group.get("name") or category, group, category)
            domains["Item Group"] = domains.get("Item Group", 0) + 1
            records_written += 1

        items = self._frappe_list(
            "Item",
            ["name", "item_code", "item_name", "item_group", "brand", "standard_rate", "valuation_rate", "disabled"],
            filters=[["disabled", "=", 0]],
            limit=500,
        )
        records_read += len(items)
        for item in items:
            sku = item.get("item_code") or item.get("name")
            if not sku:
                continue
            category = _slug(item.get("item_group"))
            price = _safe_float(item.get("standard_rate") or item.get("valuation_rate"), 1.0)
            vendor = item.get("brand") or "ERPNext"
            self._upsert_category(category, item.get("item_group") or _display(category))
            self._upsert_sku(sku, item.get("item_name") or sku, category, vendor, price)
            self._cache("Item", item.get("name") or sku, item, sku)
            domains["Item"] = domains.get("Item", 0) + 1
            records_written += 1

        warehouses = self._frappe_list("Warehouse", ["name", "warehouse_name", "company"], limit=200)
        records_read += len(warehouses)
        for warehouse in warehouses:
            store_id = _slug(warehouse.get("name"), "warehouse")
            self._cache("Warehouse", warehouse.get("name") or store_id, warehouse, store_id)
            domains["Warehouse"] = domains.get("Warehouse", 0) + 1
            records_written += 1

        bins = self._frappe_list("Bin", ["name", "item_code", "warehouse", "actual_qty", "reserved_qty"], limit=800)
        records_read += len(bins)
        # Aggregate per-warehouse Bin rows back to a single on_hand per SKU.
        # ERPNext stores stock per (item, warehouse); our spine substrate stores
        # one on_hand per SKU. Without summing here, each Bin update overwrites
        # the prior one and the final value is whichever warehouse came last.
        on_hand_by_sku: dict[str, int] = {}
        for bin_row in bins:
            sku = bin_row.get("item_code")
            if sku:
                on_hand_by_sku[sku] = on_hand_by_sku.get(sku, 0) + _safe_int(bin_row.get("actual_qty"))
            self._cache("Bin", bin_row.get("name") or f"{sku}:{bin_row.get('warehouse')}", bin_row, sku)
            domains["Bin"] = domains.get("Bin", 0) + 1
            records_written += 1
        if on_hand_by_sku:
            with conn() as c:
                for sku, total in on_hand_by_sku.items():
                    c.execute(
                        "UPDATE substrate_inventory SET on_hand = ? WHERE sku = ?",
                        (total, sku),
                    )

        for doctype, fields in {
            "Customer": ["name", "customer_name", "customer_group", "territory"],
            "Supplier": ["name", "supplier_name", "supplier_group"],
            "Purchase Order": ["name", "supplier", "status", "transaction_date", "schedule_date", "grand_total"],
        }.items():
            rows = self._frappe_list(doctype, fields, limit=300)
            records_read += len(rows)
            for row in rows:
                external_id = row.get("name")
                if not external_id:
                    continue
                self._cache(doctype, external_id, row, external_id)
                domains[doctype] = domains.get(doctype, 0) + 1
                records_written += 1

        invoices = self._frappe_list(
            "Sales Invoice",
            ["name", "customer", "posting_date", "is_pos", "status", "total_qty", "grand_total"],
            filters=[["is_pos", "=", 1]],
            limit=300,
        )
        records_read += len(invoices)
        for invoice in invoices:
            external_id = invoice.get("name")
            if not external_id:
                continue
            self._cache("POS Invoice", external_id, invoice, external_id)
            domains["POS Invoice"] = domains.get("POS Invoice", 0) + 1
            records_written += 1

        return IntegrationResult(
            status="success",
            records_read=records_read,
            records_written=records_written,
            summary={"mode": "connected", "domains": domains},
        )

    def _mock_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_written = 0
        with conn() as c:
            rows = c.execute("SELECT * FROM substrate_categories").fetchall()
            for row in rows:
                payload = dict(row)
                self._cache("Item Group", payload["category"], payload, payload["category"])
                domains["Item Group"] = domains.get("Item Group", 0) + 1
                records_written += 1
            rows = c.execute(
                "SELECT s.*, i.on_hand, i.price, i.base_price FROM substrate_skus s "
                "JOIN substrate_inventory i ON i.sku = s.sku"
            ).fetchall()
            for row in rows:
                payload = dict(row)
                self._cache("Item", payload["sku"], payload, payload["sku"])
                domains["Item"] = domains.get("Item", 0) + 1
                records_written += 1
            rows = c.execute("SELECT * FROM substrate_stores").fetchall()
            for row in rows:
                payload = dict(row)
                self._cache("Warehouse", payload["store_id"], payload, payload["store_id"])
                domains["Warehouse"] = domains.get("Warehouse", 0) + 1
                records_written += 1
            rows = c.execute("SELECT * FROM substrate_inbound_pos").fetchall()
            for row in rows:
                payload = dict(row)
                self._cache("Purchase Order", payload["po_id"], payload, payload["po_id"])
                domains["Purchase Order"] = domains.get("Purchase Order", 0) + 1
                records_written += 1
            rows = c.execute("SELECT * FROM substrate_orders ORDER BY id DESC LIMIT 80").fetchall()
            for row in rows:
                payload = dict(row)
                self._cache("POS Invoice", str(payload["id"]), payload, str(payload["id"]))
                domains["POS Invoice"] = domains.get("POS Invoice", 0) + 1
                records_written += 1
        return IntegrationResult(
            status="success",
            records_read=records_written,
            records_written=records_written,
            summary={"mode": "mock", "domains": domains},
        )

    def _cache(self, domain: str, external_id: str, payload: dict[str, Any], local_id: str | None) -> None:
        store.cache_record(self.definition.system_id, domain, str(external_id), payload, local_id=local_id)
        if local_id:
            store.record_external_ref(
                self.definition.system_id,
                domain,
                str(local_id),
                str(external_id),
                external_url=self._external_url(domain, str(external_id)),
                props={"source": "ERPNext"},
            )

    def _external_url(self, domain: str, external_id: str) -> str | None:
        base = os.getenv("ERPNEXT_BASE_URL")
        if not base:
            return None
        return f"{base.rstrip('/')}/app/{_slug(domain).replace('_', '-')}/{quote(external_id)}"

    def _upsert_category(self, category: str, display_name: str) -> None:
        with conn() as c:
            c.execute(
                "INSERT INTO substrate_categories "
                "(category, display_name, lifecycle_stage, margin_target, marketing_priority, weather_sensitivity, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(category) DO UPDATE SET display_name = excluded.display_name",
                (
                    category,
                    display_name,
                    "external ERPNext catalog",
                    0.34,
                    2,
                    0.1,
                    "Synced from ERPNext Item Group.",
                ),
            )

    def _upsert_sku(self, sku: str, name: str, category: str, vendor: str, price: float) -> None:
        with conn() as c:
            c.execute(
                "INSERT INTO substrate_skus (sku, name, category, vendor) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(sku) DO UPDATE SET name = excluded.name, category = excluded.category, vendor = excluded.vendor",
                (sku, name, category, vendor),
            )
            c.execute(
                "INSERT INTO substrate_inventory (sku, on_hand, reorder_point, price, base_price) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(sku) DO UPDATE SET price = excluded.price, base_price = excluded.base_price",
                (sku, 0, 10, price, price),
            )

    def outbound_domain(self, action_type: str) -> str:
        return {
            "promotion": "Pricing Rule",
            "store_transfer": "Stock Entry",
            "po_held": "Purchase Order",
            "po_expedited": "Purchase Order",
            "fulfillment_routing": "Sales Order",
        }.get(action_type, super().outbound_domain(action_type))

    # ----- Live outbound apply (P4) -----
    #
    # Map approved cockpit actions onto real ERPNext docs. Every doc lands as
    # a draft; nothing is auto-submitted. The operator is the one who pressed
    # "apply → external" in the cockpit drawer; we just record the intent in
    # ERPNext where it lives next to the existing chart of accounts and Bins
    # rather than only in our own outbox table.
    LIVE_ACTION_TYPES = {
        "promotion",
        "po_held",
        "po_expedited",
        "store_transfer",
        "fulfillment_routing",
    }

    def apply_outbound(self, action_id: int) -> dict[str, Any]:
        from app.integrations import store

        action = store.get_outbox_action(action_id, system_id=self.definition.system_id)
        if not action:
            return {"error": f"unknown outbox action: {action_id}"}
        if action["status"] in {"applied", "applied_mock", "draft_created"}:
            return action

        # Mock-mode and unsupported action types fall back to the base flow,
        # which records `applied_mock` / `draft_created` without external I/O.
        if not self.configured() or action["action_type"] not in self.LIVE_ACTION_TYPES:
            return super().apply_outbound(action_id)

        try:
            outcome = self._dispatch_outbound(action)
        except Exception as exc:  # pragma: no cover - exercised only with live ERPNext
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": str(exc),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": (
                        "ERPNext rejected the apply. The outbox action is left in "
                        "error state — fix the upstream payload and retry."
                    ),
                },
            )

        # `external_id is None` means the helper ran cleanly but couldn't
        # actually create / annotate anything in ERPNext — e.g. po_held with
        # an empty payload, or no Purchase Order matched (supplier, schedule_date).
        # Marking that as `draft_created` would lie about the outcome and
        # pre-empt a retry. Land the row in `error` instead so the operator
        # sees a red chip and the queue keeps the action open.
        if not outcome.get("external_id"):
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": outcome.get("message", "ERPNext apply produced no external_id"),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": outcome.get(
                        "message",
                        "ERPNext returned no external_id — nothing was written. Check the outbox payload and retry.",
                    ),
                    "details": outcome.get("details", {}),
                },
            )

        return store.update_outbox_action(
            action_id,
            status="draft_created",
            external_id=outcome["external_id"],
            result={
                "message": outcome.get("message", "Draft created in ERPNext."),
                "system_id": self.definition.system_id,
                "external_domain": action["external_domain"],
                "external_id": outcome["external_id"],
                "details": outcome.get("details", {}),
            },
        )

    def _dispatch_outbound(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = action["action_type"]
        payload = action.get("payload") or {}
        title = action.get("title") or "AI Retail OS action"
        if action_type == "promotion":
            return self._erp_create_pricing_rule(title, payload)
        if action_type in ("po_held", "po_expedited"):
            return self._erp_update_purchase_orders(title, payload, action_type)
        if action_type == "store_transfer":
            return self._erp_create_stock_entry(title, payload)
        if action_type == "fulfillment_routing":
            return self._erp_create_sales_order(title, payload)
        raise RuntimeError(f"unsupported action_type {action_type!r} for live ERPNext apply")

    def _company(self) -> str:
        return os.environ.get("ERPNEXT_COMPANY", "AI Retail OS")

    def _company_abbr(self) -> str:
        return os.environ.get("ERPNEXT_COMPANY_ABBR", "ARO")

    def _erp_create_pricing_rule(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        category = payload.get("category", "")
        discount = float(payload.get("discount_percent") or 0)
        item_group = _display(category) if category else None
        from datetime import date, timedelta

        today = date.today()
        upto = today + timedelta(days=30)
        doc: dict[str, Any] = {
            "doctype": "Pricing Rule",
            "title": title,
            "apply_on": "Item Group",
            "selling": 1,
            "buying": 0,
            "company": self._company(),
            "currency": "USD",
            "rate_or_discount": "Discount Percentage",
            "discount_percentage": discount,
            "price_or_product_discount": "Price",
            "valid_from": today.isoformat(),
            "valid_upto": upto.isoformat(),
        }
        if item_group:
            doc["item_groups"] = [{"item_group": item_group}]
        body = self._client().request(
            f"/api/resource/{quote('Pricing Rule')}", method="POST", payload=doc
        )
        name = (body.get("data") or {}).get("name") or body.get("name")
        return {
            "external_id": name,
            "message": (
                f"Pricing Rule {name} draft created — {discount:.0f}% off "
                f"{item_group or 'unspecified group'}."
            ),
            "details": {
                "name": name,
                "item_group": item_group,
                "discount_percentage": discount,
                "valid_from": today.isoformat(),
                "valid_upto": upto.isoformat(),
            },
        }

    def _erp_update_purchase_orders(
        self,
        title: str,
        payload: dict[str, Any],
        action_type: str,
    ) -> dict[str, Any]:
        pos = payload.get("pos") or []
        if not pos:
            return {
                "external_id": None,
                "message": f"No purchase-order rows in payload for {action_type}.",
                "details": {"payload_keys": list(payload.keys())},
            }
        from datetime import date, datetime as _dt, timedelta

        annotated: list[str] = []
        bumped: list[dict[str, str]] = []
        unmatched: list[dict[str, str]] = []
        reason = payload.get("reason") or ""
        for po in pos:
            vendor = po.get("vendor")
            eta = (po.get("eta") or "").split("T")[0]
            if not vendor or not eta:
                unmatched.append({"po_id": po.get("po_id"), "reason": "missing vendor or eta"})
                continue
            filters = json.dumps([["supplier", "=", vendor], ["schedule_date", "=", eta]])
            body = self._client().request(
                f"/api/resource/{quote('Purchase Order')}?filters={quote(filters)}&limit_page_length=1"
            )
            rows = body.get("data") or []
            if not rows:
                unmatched.append({"po_id": po.get("po_id"), "vendor": vendor, "eta": eta})
                continue
            po_name = rows[0]["name"]
            content = f"AI Retail OS · {action_type.replace('_', ' ')}"
            if reason:
                content += f" — {reason}"
            self._client().request(
                f"/api/resource/{quote('Comment')}",
                method="POST",
                payload={
                    "doctype": "Comment",
                    "comment_type": "Comment",
                    "reference_doctype": "Purchase Order",
                    "reference_name": po_name,
                    "content": content,
                },
            )
            annotated.append(po_name)
            if action_type == "po_expedited":
                try:
                    new_eta = (_dt.fromisoformat(eta) - timedelta(days=3)).date().isoformat()
                    self._client().request(
                        f"/api/resource/{quote('Purchase Order')}/{quote(po_name, safe='')}",
                        method="PUT",
                        payload={"schedule_date": new_eta},
                    )
                    bumped.append({"name": po_name, "from": eta, "to": new_eta})
                except Exception:
                    # Already-submitted POs need an Amend flow; the comment is
                    # still the durable record. Don't fail the whole apply.
                    pass
        if not annotated:
            return {
                "external_id": None,
                "message": (
                    f"No matching Purchase Orders found in ERPNext for {action_type} "
                    f"(checked {len(pos)} payload rows by supplier+schedule_date)."
                ),
                "details": {"unmatched": unmatched},
            }
        return {
            "external_id": annotated[0],
            "message": (
                f"Annotated {len(annotated)} Purchase Order(s) with {action_type}"
                + (f"; bumped {len(bumped)} schedule_date(s)" if bumped else "")
                + "."
            ),
            "details": {"annotated": annotated, "bumped": bumped, "unmatched": unmatched},
        }

    def _erp_create_stock_entry(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        from_store = payload.get("from_store_name") or payload.get("from_store")
        to_store = payload.get("to_store_name") or payload.get("to_store")
        qty = int(payload.get("qty") or 1)
        category = payload.get("category", "")
        sku = self._first_sku_for_category(category)
        if not sku:
            return {
                "external_id": None,
                "message": f"No representative SKU available for store_transfer on {category}.",
                "details": {"payload": payload},
            }
        if not from_store or not to_store:
            return {
                "external_id": None,
                "message": "store_transfer payload missing from_store / to_store.",
                "details": {"payload_keys": list(payload.keys())},
            }
        abbr = self._company_abbr()
        from_wh = f"{from_store} - {abbr}"
        to_wh = f"{to_store} - {abbr}"
        doc = {
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Transfer",
            "company": self._company(),
            "items": [
                {
                    "item_code": sku,
                    "qty": qty,
                    "s_warehouse": from_wh,
                    "t_warehouse": to_wh,
                }
            ],
            "remarks": (
                f"AI Retail OS store_transfer — {payload.get('reason') or 'rebalance'}"
            ),
        }
        body = self._client().request(
            f"/api/resource/{quote('Stock Entry')}", method="POST", payload=doc
        )
        name = (body.get("data") or {}).get("name") or body.get("name")
        return {
            "external_id": name,
            "message": (
                f"Stock Entry {name} (Material Transfer, draft) created — "
                f"{qty} × {sku}: {from_wh} → {to_wh}."
            ),
            "details": {
                "name": name,
                "from_warehouse": from_wh,
                "to_warehouse": to_wh,
                "sku": sku,
                "qty": qty,
            },
        }

    def _erp_create_sales_order(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        category = payload.get("category", "")
        sku = self._first_sku_for_category(category)
        if not sku:
            return {
                "external_id": None,
                "message": f"No representative SKU available for sales_order on {category}.",
                "details": {"payload": payload},
            }
        from datetime import date, timedelta

        today = date.today()
        delivery = (today + timedelta(days=7)).isoformat()
        abbr = self._company_abbr()
        doc = {
            "doctype": "Sales Order",
            "customer": "Walk-In",
            "company": self._company(),
            "currency": "USD",
            "selling_price_list": "Standard Selling",
            "delivery_date": delivery,
            "transaction_date": today.isoformat(),
            "items": [
                {
                    "item_code": sku,
                    "qty": 1,
                    "delivery_date": delivery,
                    "warehouse": f"Stores - {abbr}",
                }
            ],
        }
        body = self._client().request(
            f"/api/resource/{quote('Sales Order')}", method="POST", payload=doc
        )
        name = (body.get("data") or {}).get("name") or body.get("name")
        return {
            "external_id": name,
            "message": (
                f"Sales Order {name} draft created (fulfillment_routing exemplar, "
                f"qty 1 × {sku})."
            ),
            "details": {
                "name": name,
                "category": category,
                "sku": sku,
                "delivery_date": delivery,
                "strategy": payload.get("recommended_strategy"),
            },
        }

    def _first_sku_for_category(self, category: str) -> str | None:
        if not category:
            return None
        with conn() as c:
            row = c.execute(
                "SELECT s.sku FROM substrate_skus s "
                "JOIN substrate_inventory i ON i.sku = s.sku "
                "WHERE s.category = ? ORDER BY i.on_hand DESC LIMIT 1",
                (category,),
            ).fetchone()
        return row["sku"] if row else None

_MAUTIC_MARKER_RE = re.compile(r"\[retail-os:([a-zA-Z0-9_\-]+)\]")


def _coerce_id(raw: Any) -> str | None:
    """Validate and stringify a Mautic row id.

    `str(None)` is the truthy string "None" — a missing id would otherwise
    cache rows under a fake external_id, and multiple malformed rows would
    collide on it. Validate the raw value first, *then* cast.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        # JSON bools shouldn't appear in id positions; treat as malformed.
        return None
    if isinstance(raw, (int, float)):
        return str(raw)
    if isinstance(raw, str):
        s = raw.strip()
        return s or None
    return None


class MauticAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="mautic",
        display_name="Mautic",
        domain="Marketing Automation",
        docs_url="https://devdocs.mautic.org/en/7.0/",
        env_keys=("MAUTIC_BASE_URL",),
        notes="Segments, email/campaign drafts, and webhook-based campaign telemetry.",
    )

    def configured(self) -> bool:
        # Beyond the base-class env-key check, require either the pre-baked
        # MAUTIC_BASIC_TOKEN or the user/password pair the bootstrap script
        # prints. Without auth there is nothing to talk to — so don't claim
        # the adapter is configured.
        if not super().configured():
            return False
        if os.getenv("MAUTIC_BASIC_TOKEN", "").strip():
            return True
        return bool(
            os.getenv("MAUTIC_USERNAME", "").strip()
            and os.getenv("MAUTIC_PASSWORD", "").strip()
        )

    def _client(self) -> JsonHttpClient:
        headers: dict[str, str] = {}
        token = os.getenv("MAUTIC_BASIC_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Basic {token}"
        else:
            raw = f"{os.environ['MAUTIC_USERNAME']}:{os.environ['MAUTIC_PASSWORD']}"
            headers["Authorization"] = f"Basic {b64encode(raw.encode()).decode()}"
        return JsonHttpClient(os.environ["MAUTIC_BASE_URL"], headers=headers)

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:  # pragma: no cover - live-system path
            data = self._client().request("/api/contacts?limit=1")
            return IntegrationResult(status="connected", summary={"contacts_seen": len(data.get("contacts", []))})
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    def sync_inbound(self) -> IntegrationResult:
        if not self.configured():
            return self._mock_sync()
        try:
            return self._live_sync()
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    # ----- live ------------------------------------------------------------

    def _mautic_list(
        self,
        endpoint: str,
        key: str,
        limit: int = 200,
        max_rows: int | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """GET /api/<endpoint> with pagination, normalised to a flat list.

        Mautic returns rows keyed by id (`{"42": {...}}`) plus a `total`
        field. A single request only returns one page (`limit` rows); we
        walk `start` until we've consumed `total` (or hit a short page
        when `total` isn't returned).

        Returns `(rows, truncated)`. `max_rows=None` (the default and
        what `_live_sync` passes) means no cap — sync is comprehensive
        by design. A caller that does pass `max_rows` and hits it gets
        `truncated=True`, and the live-sync caller surfaces that in the
        result `summary` so it isn't silent.
        """
        out: list[dict[str, Any]] = []
        start = 0
        truncated = False
        while True:
            page_limit = (
                limit if max_rows is None else min(limit, max(1, max_rows - len(out)))
            )
            data = self._client().request(
                f"/api/{endpoint}?limit={page_limit}&start={start}"
            )
            rows = data.get(key) or {}
            if isinstance(rows, dict):
                page = list(rows.values())
            elif isinstance(rows, list):
                page = rows
            else:
                page = []
            if not page:
                break
            out.extend(page)
            # Prefer Mautic's authoritative `total`; fall back to the
            # short-page heuristic when total isn't returned.
            total_raw = data.get("total")
            try:
                total = int(total_raw) if total_raw is not None else None
            except (TypeError, ValueError):
                total = None
            if total is not None and start + len(page) >= total:
                break
            if len(page) < page_limit:
                break
            if max_rows is not None and len(out) >= max_rows:
                # Cap reached but the API said there's more — flag it loudly
                # so the result summary can mark this domain as truncated.
                truncated = total is None or total > len(out)
                break
            start += page_limit
        return out, truncated

    def _live_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        truncated_domains: list[str] = []
        records_read = 0
        records_written = 0

        # Pull every page (no row cap by default — sync is comprehensive). If
        # an explicit cap is ever wired in via env later, the helper marks
        # truncated=True and we surface that in `summary["truncated_domains"]`
        # so callers don't quietly act on an incomplete cache.
        segs, seg_trunc = self._mautic_list("segments", "lists")
        contacts, contact_trunc = self._mautic_list("contacts", "contacts", limit=500)
        campaigns, cmp_trunc = self._mautic_list("campaigns", "campaigns")

        # Segments: alias == seg_id with - replaced by _ (see infra/mautic/seed.py).
        # Recover the substrate segment_id by reversing that mapping so the
        # external_refs row links the Mautic list back to the spine segment.
        for seg in segs:
            external_id = _coerce_id(seg.get("id"))
            if external_id is None:
                continue
            alias = (seg.get("alias") or "").strip()
            local_id = alias.replace("_", "-") if alias else None
            self._cache("Segment", external_id, seg, local_id)
            domains["Segment"] = domains.get("Segment", 0) + 1
            records_written += 1
            records_read += 1
        if seg_trunc:
            truncated_domains.append("Segment")

        # Contacts: local_id is email since that's deterministic across our
        # seeded personas. If a contact has no email we still cache the row
        # but skip the external_ref (no clean local key to anchor it).
        for contact in contacts:
            external_id = _coerce_id(contact.get("id"))
            if external_id is None:
                continue
            fields = (contact.get("fields") or {}).get("core") or {}
            email = (fields.get("email") or {}).get("value") or contact.get("email")
            self._cache("Contact", external_id, contact, email)
            domains["Contact"] = domains.get("Contact", 0) + 1
            records_written += 1
            records_read += 1
        if contact_trunc:
            truncated_domains.append("Contact")

        # Campaigns: recover the substrate campaign_id from the
        # `[retail-os:<id>]` marker our seeder embeds in description.
        # If the marker isn't present (operator-authored campaign) we still
        # cache the row but with no local_id.
        for cmp in campaigns:
            external_id = _coerce_id(cmp.get("id"))
            if external_id is None:
                continue
            description = cmp.get("description") or ""
            match = _MAUTIC_MARKER_RE.search(description)
            local_id = match.group(1) if match else None
            self._cache("Campaign", external_id, cmp, local_id)
            domains["Campaign"] = domains.get("Campaign", 0) + 1
            records_written += 1
            records_read += 1
        if cmp_trunc:
            truncated_domains.append("Campaign")

        summary: dict[str, Any] = {"mode": "connected", "domains": domains}
        if truncated_domains:
            summary["truncated_domains"] = truncated_domains
        # Status downgrades to "partial" so callers can branch on
        # "this snapshot is incomplete" without parsing summary keys.
        status = "partial" if truncated_domains else "success"
        return IntegrationResult(
            status=status,
            records_read=records_read,
            records_written=records_written,
            summary=summary,
        )

    # ----- mock ------------------------------------------------------------

    def _mock_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_written = 0
        with conn() as c:
            segments = c.execute("SELECT * FROM substrate_customer_segments").fetchall()
            campaigns = c.execute("SELECT * FROM substrate_campaigns").fetchall()
        for row in segments:
            payload = dict(row)
            store.cache_record("mautic", "Segment", payload["segment_id"], payload, payload["segment_id"])
            store.record_external_ref("mautic", "Segment", payload["segment_id"], payload["segment_id"], props={"source": "mock"})
            domains["Segment"] = domains.get("Segment", 0) + 1
            records_written += 1
        for row in campaigns:
            payload = dict(row)
            store.cache_record("mautic", "Campaign", payload["campaign_id"], payload, payload["campaign_id"])
            store.record_external_ref("mautic", "Campaign", payload["campaign_id"], payload["campaign_id"], props={"source": "mock"})
            domains["Campaign"] = domains.get("Campaign", 0) + 1
            records_written += 1
        return IntegrationResult(
            status="success",
            records_read=records_written,
            records_written=records_written,
            summary={"mode": "mock", "domains": domains},
        )

    def _cache(self, domain: str, external_id: str, payload: dict[str, Any], local_id: str | None) -> None:
        store.cache_record(self.definition.system_id, domain, str(external_id), payload, local_id=local_id)
        if local_id:
            store.record_external_ref(
                self.definition.system_id,
                domain,
                str(local_id),
                str(external_id),
                external_url=self._external_url(domain, str(external_id)),
                props={"source": "Mautic"},
            )

    def _external_url(self, domain: str, external_id: str) -> str | None:
        base = os.getenv("MAUTIC_BASE_URL")
        if not base:
            return None
        path = {
            "Segment": "s/segments/view",
            "Contact": "s/contacts/view",
            "Campaign": "s/campaigns/view",
        }.get(domain)
        if not path:
            return None
        return f"{base.rstrip('/')}/{path}/{quote(external_id)}"

    def outbound_domain(self, action_type: str) -> str:
        return {
            "campaign_brief": "Segment Email",
            "campaign_launch": "Campaign",
            "campaign_measurement": "Campaign Report",
        }.get(action_type, super().outbound_domain(action_type))

    # ----- live outbound apply --------------------------------------------

    # Cockpit actions that map onto real Mautic objects. Everything else falls
    # back to the base mock-apply behaviour. Same draft-only stance as ERPNext:
    # the operator pressed "apply", so we record the intent in Mautic; we
    # don't auto-publish.
    LIVE_ACTION_TYPES = {"campaign_launch", "campaign_brief"}

    def apply_outbound(self, action_id: int) -> dict[str, Any]:
        from app.integrations import store

        action = store.get_outbox_action(action_id, system_id=self.definition.system_id)
        if not action:
            return {"error": f"unknown outbox action: {action_id}"}
        if action["status"] in {"applied", "applied_mock", "draft_created"}:
            return action

        # Mock-mode and unsupported action_types fall through to the base
        # adapter, which records `applied_mock` / `draft_created` without I/O.
        if not self.configured() or action["action_type"] not in self.LIVE_ACTION_TYPES:
            return super().apply_outbound(action_id)

        try:
            outcome = self._dispatch_outbound(action)
        except Exception as exc:  # pragma: no cover - exercised only with live Mautic
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": str(exc),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": (
                        "Mautic rejected the apply. The outbox action is left "
                        "in error state — fix the upstream payload and retry."
                    ),
                },
            )

        # No external_id == helper ran cleanly but couldn't actually write
        # anything (e.g. campaign_launch with no campaign_id, or Mautic
        # returned an empty body). Land in `error` instead of lying with
        # `draft_created`.
        if not outcome.get("external_id"):
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": outcome.get("message", "Mautic apply produced no external_id"),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": outcome.get(
                        "message",
                        "Mautic returned no external_id — nothing was written. Check the outbox payload and retry.",
                    ),
                    "details": outcome.get("details", {}),
                },
            )

        return store.update_outbox_action(
            action_id,
            status="draft_created",
            external_id=outcome["external_id"],
            result={
                "message": outcome.get("message", "Draft created in Mautic."),
                "system_id": self.definition.system_id,
                "external_domain": action["external_domain"],
                "external_id": outcome["external_id"],
                "details": outcome.get("details", {}),
            },
        )

    def _dispatch_outbound(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = action["action_type"]
        payload = action.get("payload") or {}
        title = action.get("title") or "AI Retail OS action"
        if action_type == "campaign_launch":
            return self._mautic_create_campaign(title, payload)
        if action_type == "campaign_brief":
            return self._mautic_ensure_segment(title, payload)
        raise RuntimeError(f"unsupported action_type {action_type!r} for live Mautic apply")

    def _post(self, endpoint: str, body: dict[str, Any], key: str) -> dict[str, Any]:
        data = self._client().request(f"/api/{endpoint}/new", method="POST", payload=body)
        # Mautic POST /new returns {"<key-singular>": {"id": …, …}}.
        # The key passed in is whatever the dispatcher knows is correct
        # ("campaign", "list" for segments — yes, segments-singular is `list`).
        item = data.get(key) or {}
        return item if isinstance(item, dict) else {}

    def _mautic_create_campaign(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        """`campaign_launch` → POST /api/campaigns/new (draft).

        We embed the spine `campaign_id` as `[retail-os:<id>]` in the
        description so a follow-up sync round-trips it back to the same
        substrate row (P3 already parses that marker). Drafts only — Mautic
        campaigns need events / triggers before they can run; the operator
        wires those up in the UI.
        """
        campaign_id = payload.get("campaign_id") or ""
        if not campaign_id:
            return {
                "external_id": None,
                "message": "campaign_launch payload has no campaign_id; nothing to apply.",
            }
        marker = f"[retail-os:{campaign_id}]"
        description = "\n".join(
            [
                marker,
                f"Category: {payload.get('category', '')}",
                f"Segment: {payload.get('segment_id', '')}",
                f"Channel: {payload.get('channel', '')}",
                f"Offer: {payload.get('offer', '')}",
                f"Projected lift: {payload.get('projected_lift', 0)}",
                f"Projected ROI: {payload.get('projected_roi', 0)}",
                f"Budget: {payload.get('budget', 0)}",
            ]
        )
        item = self._post(
            "campaigns",
            {
                "name": title,
                "description": description,
                "isPublished": False,
            },
            "campaign",
        )
        external_id = _coerce_id(item.get("id"))
        if external_id is None:
            return {
                "external_id": None,
                "message": "Mautic campaign creation returned no id.",
                "details": item,
            }
        return {
            "external_id": external_id,
            "message": f"Mautic campaign draft created (id={external_id}).",
            "details": {"campaign_id": campaign_id, "marker": marker},
        }

    def _mautic_ensure_segment(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        """`campaign_brief` → ensure a Mautic Segment exists for the brief.

        Uses the same alias scheme as the seed (segment_id with `-` → `_`),
        so re-applying the brief lands on the same Mautic list rather than
        creating a duplicate. Looks up by alias first; only POSTs if not
        present.
        """
        segment_id = payload.get("segment_id") or ""
        if not segment_id:
            return {
                "external_id": None,
                "message": "campaign_brief payload has no segment_id; nothing to apply.",
            }
        alias = segment_id.replace("-", "_")
        existing = self._mautic_find_one(
            "segments", "lists", [("alias", "eq", alias)]
        )
        if existing is not None:
            external_id = _coerce_id(existing.get("id"))
            if external_id is not None:
                return {
                    "external_id": external_id,
                    "message": f"Mautic segment already exists (id={external_id}).",
                    "details": {"segment_id": segment_id, "alias": alias, "reused": True},
                }
        item = self._post(
            "segments",
            {
                "name": payload.get("segment_name") or title,
                "alias": alias,
                "publicName": payload.get("segment_name") or title,
                "description": (
                    f"[retail-os:{segment_id}] "
                    f"Category: {payload.get('category', '')} · "
                    f"Channel: {payload.get('channel', '')} · "
                    f"Offer: {payload.get('offer', '')}"
                ),
                "isPublished": True,
                "isGlobal": True,
            },
            "list",
        )
        external_id = _coerce_id(item.get("id"))
        if external_id is None:
            return {
                "external_id": None,
                "message": "Mautic segment creation returned no id.",
                "details": item,
            }
        return {
            "external_id": external_id,
            "message": f"Mautic segment created (id={external_id}).",
            "details": {"segment_id": segment_id, "alias": alias},
        }

    def _mautic_find_one(
        self,
        endpoint: str,
        key: str,
        filters: list[tuple[str, str, str]],
    ) -> dict[str, Any] | None:
        """Mautic column filter for idempotency lookups (mirrors seed.py)."""
        params: list[tuple[str, str]] = [("limit", "1")]
        for i, (col, expr, val) in enumerate(filters):
            params.append((f"where[{i}][col]", col))
            params.append((f"where[{i}][expr]", expr))
            params.append((f"where[{i}][val]", val))
        qs = urlencode(params)
        data = self._client().request(f"/api/{endpoint}?{qs}")
        rows = data.get(key) or {}
        if isinstance(rows, dict) and rows:
            return next(iter(rows.values()))
        if isinstance(rows, list) and rows:
            return rows[0]
        return None


class MedusaAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="medusa",
        display_name="Medusa",
        domain="Ecommerce / OMS",
        docs_url="https://docs.medusajs.com/",
        env_keys=("MEDUSA_BASE_URL",),
        notes="Online orders, channels, inventory locations, reservations, and fulfillment routing.",
    )

    # Cached admin token. Medusa v2 returns a JWT from
    # POST /auth/user/emailpass; we lazy-login on first /admin/* call
    # and cache it for the duration of the adapter instance.
    _admin_token: str | None = None

    def configured(self) -> bool:
        # Beyond the base-class env-key check, require the admin
        # credentials we actually need to authenticate. Without them
        # there's nothing to log into, so don't claim configured.
        if not super().configured():
            return False
        return bool(
            os.getenv("MEDUSA_ADMIN_EMAIL", "").strip()
            and os.getenv("MEDUSA_ADMIN_PASSWORD", "").strip()
        )

    def _login(self) -> str:
        """POST /auth/user/emailpass → token. Cached on the instance."""
        if self._admin_token:
            return self._admin_token
        client = JsonHttpClient(os.environ["MEDUSA_BASE_URL"])
        data = client.request(
            "/auth/user/emailpass",
            method="POST",
            payload={
                "email": os.environ["MEDUSA_ADMIN_EMAIL"],
                "password": os.environ["MEDUSA_ADMIN_PASSWORD"],
            },
        )
        token = (data or {}).get("token")
        if not token:
            raise RuntimeError(f"medusa login returned no token: {data}")
        self._admin_token = token
        return token

    def _client(self) -> JsonHttpClient:
        token = self._login()
        return JsonHttpClient(
            os.environ["MEDUSA_BASE_URL"],
            headers={"Authorization": f"Bearer {token}"},
        )

    def _admin_request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Wrap a /admin/* call with one re-login + retry on 401.

        Medusa v2 admin tokens are JWTs with a finite lifetime (and can
        be revoked admin-side). A long-running cockpit process would
        otherwise keep using a stale token after expiry and `sync_inbound`
        would stay stuck returning `error` until restart. On 401 we drop
        the cached token, re-login, and retry once; any further 4xx/5xx
        raises so the live-sync wrapper can land it as `error`.
        """
        try:
            return self._client().request(path, method=method, payload=payload)
        except HTTPError as e:
            if e.code != 401:
                raise
            self._admin_token = None
            return self._client().request(path, method=method, payload=payload)

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:  # pragma: no cover - live-system path
            data = self._admin_request("/admin/products?limit=1")
            return IntegrationResult(
                status="connected",
                summary={"products_seen": len(data.get("products", []))},
            )
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    def sync_inbound(self) -> IntegrationResult:
        if not self.configured():
            return self._mock_sync()
        try:
            return self._live_sync()
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    # ----- live ------------------------------------------------------------

    def _admin_list(
        self,
        endpoint: str,
        key: str,
        limit: int = 100,
        max_rows: int | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """GET /admin/<endpoint> with offset/limit pagination.

        Returns (rows, truncated). Mirrors Mautic's `_mautic_list`:
        max_rows=None means uncapped (the default through `_live_sync`),
        no silent truncation; an explicit cap that gets hit returns
        truncated=True so the caller can downgrade status to "partial".
        """
        out: list[dict[str, Any]] = []
        offset = 0
        truncated = False
        while True:
            page_limit = (
                limit if max_rows is None else min(limit, max(1, max_rows - len(out)))
            )
            qs = urlencode({"limit": page_limit, "offset": offset})
            data = self._admin_request(f"/admin/{endpoint}?{qs}")
            rows = data.get(key) or []
            if not isinstance(rows, list) or not rows:
                break
            out.extend(rows)
            count_raw = data.get("count")
            try:
                count = int(count_raw) if count_raw is not None else None
            except (TypeError, ValueError):
                count = None
            if count is not None and offset + len(rows) >= count:
                break
            if len(rows) < page_limit:
                break
            if max_rows is not None and len(out) >= max_rows:
                truncated = count is None or count > len(out)
                break
            offset += page_limit
        return out, truncated

    def _live_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        truncated_domains: list[str] = []
        records_read = 0
        records_written = 0

        # Sales channels — small, no cap.
        channels, ch_trunc = self._admin_list("sales-channels", "sales_channels")
        for ch in channels:
            external_id = _coerce_id(ch.get("id"))
            if external_id is None:
                continue
            local_id = (ch.get("metadata") or {}).get("retail_os_channel_id")
            self._cache("Sales Channel", external_id, ch, local_id)
            domains["Sales Channel"] = domains.get("Sales Channel", 0) + 1
            records_written += 1
            records_read += 1
        if ch_trunc:
            truncated_domains.append("Sales Channel")

        # Stock locations — keyed back to substrate store_id via the
        # metadata.retail_os_store_id stash the seed wrote.
        locations, loc_trunc = self._admin_list("stock-locations", "stock_locations")
        for loc in locations:
            external_id = _coerce_id(loc.get("id"))
            if external_id is None:
                continue
            local_id = (loc.get("metadata") or {}).get("retail_os_store_id")
            self._cache("Stock Location", external_id, loc, local_id)
            domains["Stock Location"] = domains.get("Stock Location", 0) + 1
            records_written += 1
            records_read += 1
        if loc_trunc:
            truncated_domains.append("Stock Location")

        # Products + variants. local_id is the SKU — recovered from the
        # variant's `sku` field (preferred) or the metadata stash.
        products, prod_trunc = self._admin_list("products", "products")
        for prod in products:
            external_id = _coerce_id(prod.get("id"))
            if external_id is None:
                continue
            variants = prod.get("variants") or []
            sku = None
            if variants:
                sku = variants[0].get("sku")
            sku = sku or (prod.get("metadata") or {}).get("retail_os_sku")
            self._cache("Product", external_id, prod, sku)
            domains["Product"] = domains.get("Product", 0) + 1
            records_written += 1
            records_read += 1
        if prod_trunc:
            truncated_domains.append("Product")

        # Orders — flat list. local_id stays None; we don't have a clean
        # round-trip key for substrate_orders without inventing one.
        orders, ord_trunc = self._admin_list("orders", "orders")
        for order in orders:
            external_id = _coerce_id(order.get("id"))
            if external_id is None:
                continue
            self._cache("Order", external_id, order, None)
            domains["Order"] = domains.get("Order", 0) + 1
            records_written += 1
            records_read += 1
        if ord_trunc:
            truncated_domains.append("Order")

        summary: dict[str, Any] = {"mode": "connected", "domains": domains}
        if truncated_domains:
            summary["truncated_domains"] = truncated_domains
        status = "partial" if truncated_domains else "success"
        return IntegrationResult(
            status=status,
            records_read=records_read,
            records_written=records_written,
            summary=summary,
        )

    # ----- mock ------------------------------------------------------------

    def _mock_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_written = 0
        with conn() as c:
            for row in c.execute("SELECT * FROM substrate_orders ORDER BY id DESC LIMIT 120").fetchall():
                payload = dict(row)
                store.cache_record("medusa", "Order", str(payload["id"]), payload, str(payload["id"]))
                domains["Order"] = domains.get("Order", 0) + 1
                records_written += 1
            for row in c.execute("SELECT * FROM substrate_store_inventory LIMIT 200").fetchall():
                payload = dict(row)
                external_id = f"{payload['store_id']}:{payload['sku']}"
                store.cache_record("medusa", "Inventory Level", external_id, payload, payload["sku"])
                domains["Inventory Level"] = domains.get("Inventory Level", 0) + 1
                records_written += 1
        return IntegrationResult(
            status="success",
            records_read=records_written,
            records_written=records_written,
            summary={"mode": "mock", "domains": domains},
        )

    def _cache(self, domain: str, external_id: str, payload: dict[str, Any], local_id: str | None) -> None:
        store.cache_record(self.definition.system_id, domain, str(external_id), payload, local_id=local_id)
        if local_id:
            store.record_external_ref(
                self.definition.system_id,
                domain,
                str(local_id),
                str(external_id),
                external_url=self._external_url(domain, str(external_id)),
                props={"source": "Medusa"},
            )

    def _external_url(self, domain: str, external_id: str) -> str | None:
        base = os.getenv("MEDUSA_BASE_URL")
        if not base:
            return None
        path = {
            "Sales Channel": "app/settings/sales-channels",
            "Stock Location": "app/settings/locations",
            "Product": "app/products",
            "Order": "app/orders",
        }.get(domain)
        if not path:
            return None
        return f"{base.rstrip('/')}/{path}/{quote(external_id)}"

    def outbound_domain(self, action_type: str) -> str:
        return {"fulfillment_routing": "Fulfillment", "store_transfer": "Reservation"}.get(
            action_type, super().outbound_domain(action_type)
        )

    # ----- live outbound apply --------------------------------------------

    # Cockpit actions that map onto real Medusa objects. Everything else
    # falls back to the base mock-apply behaviour.
    #
    # Medusa v2 doesn't have a built-in inter-location transfer concept,
    # and our outbox payloads don't carry concrete order ids — so for both
    # supported types we record the action as auditable metadata on an
    # existing Medusa entity rather than inventing fake orders / inventory
    # items just to look impressive. Operator can read the audit trail
    # directly from the Medusa admin UI.
    LIVE_ACTION_TYPES = {"store_transfer", "fulfillment_routing"}

    def apply_outbound(self, action_id: int) -> dict[str, Any]:
        from app.integrations import store

        action = store.get_outbox_action(action_id, system_id=self.definition.system_id)
        if not action:
            return {"error": f"unknown outbox action: {action_id}"}
        if action["status"] in {"applied", "applied_mock", "draft_created"}:
            return action

        if not self.configured() or action["action_type"] not in self.LIVE_ACTION_TYPES:
            return super().apply_outbound(action_id)

        try:
            outcome = self._dispatch_outbound(action)
        except Exception as exc:  # pragma: no cover - exercised only with live Medusa
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": str(exc),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": (
                        "Medusa rejected the apply. The outbox action is left "
                        "in error state — fix the upstream payload and retry."
                    ),
                },
            )

        if not outcome.get("external_id"):
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": outcome.get("message", "Medusa apply produced no external_id"),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": outcome.get(
                        "message",
                        "Medusa returned no external_id — nothing was written. Check the outbox payload and retry.",
                    ),
                    "details": outcome.get("details", {}),
                },
            )

        return store.update_outbox_action(
            action_id,
            status="draft_created",
            external_id=outcome["external_id"],
            result={
                "message": outcome.get("message", "Recorded action in Medusa."),
                "system_id": self.definition.system_id,
                "external_domain": action["external_domain"],
                "external_id": outcome["external_id"],
                "details": outcome.get("details", {}),
            },
        )

    def _dispatch_outbound(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = action["action_type"]
        payload = action.get("payload") or {}
        title = action.get("title") or "AI Retail OS action"
        if action_type == "store_transfer":
            return self._medusa_record_transfer(title, payload)
        if action_type == "fulfillment_routing":
            return self._medusa_record_routing(title, payload)
        raise RuntimeError(f"unsupported action_type {action_type!r} for live Medusa apply")

    @staticmethod
    def _payload_marker(payload: dict[str, Any]) -> str:
        """Stable per-payload marker for idempotent metadata appends.

        Re-applying the same outbox row should land at the existing log
        entry instead of duplicating. Sorted-keys sha256 means same
        payload → same marker, even across processes.
        """
        import hashlib

        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return f"p{digest[:12]}"

    def _medusa_record_transfer(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        """`store_transfer` → append the transfer to the from-store stock
        location's `metadata.retail_os_pending_transfers` array.

        Medusa v2 has no inter-location transfer primitive; metadata
        stash is the cleanest auditable signal. We look up the location
        by `metadata.retail_os_store_id` (the seed stashed it in P2),
        fail clearly if it isn't there, and dedupe by payload-hash
        marker so re-runs are idempotent.
        """
        from_store = (payload.get("from_store") or "").strip()
        if not from_store:
            return {
                "external_id": None,
                "message": "store_transfer payload has no from_store; nothing to apply.",
            }
        target = self._find_location_by_store_id(from_store)
        if target is None:
            return {
                "external_id": None,
                "message": (
                    f"no Medusa stock_location found for retail_os_store_id="
                    f"{from_store}; run `make medusa-seed`."
                ),
            }
        location_id = str(target["id"])
        meta = dict(target.get("metadata") or {})
        log = list(meta.get("retail_os_pending_transfers") or [])
        marker = self._payload_marker(payload)
        if any((isinstance(e, dict) and e.get("marker") == marker) for e in log):
            return {
                "external_id": location_id,
                "message": f"transfer already recorded on stock_location {location_id}.",
                "details": {"marker": marker, "reused": True},
            }
        log.append(
            {
                "marker": marker,
                "title": title,
                "to_store": payload.get("to_store"),
                "category": payload.get("category"),
                "qty": payload.get("qty"),
                "reason": payload.get("reason"),
            }
        )
        meta["retail_os_pending_transfers"] = log
        self._admin_request(
            f"/admin/stock-locations/{quote(location_id)}",
            method="POST",
            payload={"metadata": meta},
        )
        return {
            "external_id": location_id,
            "message": f"Recorded transfer on stock_location {location_id}.",
            "details": {"marker": marker, "from_store": from_store},
        }

    def _medusa_record_routing(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        """`fulfillment_routing` → append to the Retail Demo sales channel's
        `metadata.retail_os_routing_log`. Same marker-dedupe pattern.
        """
        target = self._find_sales_channel_by_name("Retail Demo")
        if target is None:
            return {
                "external_id": None,
                "message": (
                    "no 'Retail Demo' sales channel found; run `make medusa-seed`."
                ),
            }
        channel_id = str(target["id"])
        meta = dict(target.get("metadata") or {})
        log = list(meta.get("retail_os_routing_log") or [])
        marker = self._payload_marker(payload)
        if any((isinstance(e, dict) and e.get("marker") == marker) for e in log):
            return {
                "external_id": channel_id,
                "message": f"routing already recorded on sales_channel {channel_id}.",
                "details": {"marker": marker, "reused": True},
            }
        log.append(
            {
                "marker": marker,
                "title": title,
                "category": payload.get("category"),
                "strategy": payload.get("recommended_strategy"),
                "guardrail": payload.get("guardrail"),
            }
        )
        meta["retail_os_routing_log"] = log
        self._admin_request(
            f"/admin/sales-channels/{quote(channel_id)}",
            method="POST",
            payload={"metadata": meta},
        )
        return {
            "external_id": channel_id,
            "message": f"Recorded routing on sales_channel {channel_id}.",
            "details": {"marker": marker},
        }

    def _find_location_by_store_id(self, store_id: str) -> dict[str, Any] | None:
        rows, _ = self._admin_list("stock-locations", "stock_locations")
        for row in rows:
            meta = row.get("metadata") or {}
            if meta.get("retail_os_store_id") == store_id:
                return row
        return None

    def _find_sales_channel_by_name(self, name: str) -> dict[str, Any] | None:
        rows, _ = self._admin_list("sales-channels", "sales_channels")
        for row in rows:
            if row.get("name") == name:
                return row
        return None


_OPENBOXES_MARKER_RE = re.compile(r"\[retail-os:([a-zA-Z0-9_\-]+)\]")


class OpenBoxesAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="openboxes",
        display_name="OpenBoxes",
        domain="Warehouse / Supply Chain",
        docs_url="https://docs.openboxes.com/en/latest/",
        env_keys=("OPENBOXES_BASE_URL",),
        notes="Warehouse/DC inventory, receiving, stock movements, and inbound supplier realism.",
    )

    _auth_token: str | None = None

    def configured(self) -> bool:
        # Beyond MEDUSA_BASE_URL / mirror, require either a pre-baked
        # OPENBOXES_API_TOKEN or the user/password pair the bootstrap
        # script prints.
        if not super().configured():
            return False
        if os.getenv("OPENBOXES_API_TOKEN", "").strip():
            return True
        return bool(
            os.getenv("OPENBOXES_USERNAME", "").strip()
            and os.getenv("OPENBOXES_PASSWORD", "").strip()
        )

    def _login(self) -> str:
        """`POST /api/login` → token. Cached on the instance.

        Operator can supply OPENBOXES_API_TOKEN to bypass the login flow
        (useful for CI / shared environments where the bootstrap admin
        password is unknown).
        """
        if self._auth_token:
            return self._auth_token
        env_token = os.getenv("OPENBOXES_API_TOKEN", "").strip()
        if env_token:
            self._auth_token = env_token
            return env_token
        client = JsonHttpClient(os.environ["OPENBOXES_BASE_URL"])
        data = client.request(
            "/api/login",
            method="POST",
            payload={
                "username": os.environ["OPENBOXES_USERNAME"],
                "password": os.environ["OPENBOXES_PASSWORD"],
            },
        )
        # OpenBoxes returns either {"token": "..."} or wraps in {"data": {...}}
        # depending on the minor; handle both.
        token = (
            (data or {}).get("token")
            or ((data or {}).get("data") or {}).get("token")
        )
        if not token:
            raise RuntimeError(f"openboxes login returned no token: {data}")
        self._auth_token = token
        return token

    def _client(self) -> JsonHttpClient:
        token = self._login()
        return JsonHttpClient(
            os.environ["OPENBOXES_BASE_URL"],
            headers={"X-Auth-Token": token},
        )

    def _admin_request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Wrap /api/* calls with one re-login + retry on 401 — same shape
        as MedusaAdapter._admin_request.
        """
        try:
            return self._client().request(path, method=method, payload=payload)
        except HTTPError as e:
            if e.code != 401:
                raise
            self._auth_token = None
            return self._client().request(path, method=method, payload=payload)

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:  # pragma: no cover - live-system path
            data = self._admin_request("/api/locations?max=1")
            count = len(data.get("data") or data.get("locations") or [])
            return IntegrationResult(status="connected", summary={"locations_seen": count})
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    def sync_inbound(self) -> IntegrationResult:
        if not self.configured():
            return self._mock_sync()
        try:
            return self._live_sync()
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    # ----- live ------------------------------------------------------------

    def _api_list(self, endpoint: str, *, key: str = "data", query: dict | None = None) -> list[dict[str, Any]]:
        """GET /api/<endpoint>. OpenBoxes 0.9.x returns the list under
        either `data` or the resource's own key — we accept both shapes.
        Single-page only — the demo seed creates < 100 rows of each
        domain and the cockpit doesn't currently paginate against
        openboxes.
        """
        qs = urlencode(query or {})
        path = f"/api/{endpoint}" + (f"?{qs}" if qs else "")
        data = self._admin_request(path)
        rows = data.get(key) or data.get(endpoint) or data.get("data") or []
        if isinstance(rows, dict):
            return list(rows.values())
        return rows if isinstance(rows, list) else []

    def _live_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_read = 0
        records_written = 0

        # Locations: round-trip via the [retail-os:<store_id>] marker
        # the seed embeds in description (mirror of Medusa's metadata
        # stash, since OpenBoxes' Location domain doesn't have a
        # general-purpose JSON metadata field).
        locations = self._api_list("locations")
        for loc in locations:
            external_id = _coerce_id(loc.get("id"))
            if external_id is None:
                continue
            description = loc.get("description") or ""
            match = _OPENBOXES_MARKER_RE.search(description)
            local_id = match.group(1) if match else None
            self._cache("Location", external_id, loc, local_id)
            domains["Location"] = domains.get("Location", 0) + 1
            records_written += 1
            records_read += 1

        # Products: local_id is `productCode` (== substrate SKU when
        # seeded; operator-created products fall through with no local_id).
        products = self._api_list("products")
        for prod in products:
            external_id = _coerce_id(prod.get("id"))
            if external_id is None:
                continue
            local_id = prod.get("productCode") or prod.get("product_code")
            self._cache("Product", external_id, prod, local_id)
            domains["Product"] = domains.get("Product", 0) + 1
            records_written += 1
            records_read += 1

        # Inbound shipments: we GET-with-direction-INBOUND when the
        # endpoint accepts it, fall back to the unfiltered list.
        try:
            shipments = self._api_list("shipments", query={"direction": "INBOUND"})
        except Exception:
            shipments = self._api_list("shipments")
        for ship in shipments:
            external_id = _coerce_id(ship.get("id"))
            if external_id is None:
                continue
            self._cache("Inbound Shipment", external_id, ship, ship.get("name"))
            domains["Inbound Shipment"] = domains.get("Inbound Shipment", 0) + 1
            records_written += 1
            records_read += 1

        return IntegrationResult(
            status="success",
            records_read=records_read,
            records_written=records_written,
            summary={"mode": "connected", "domains": domains},
        )

    # ----- mock ------------------------------------------------------------

    def _mock_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_written = 0
        with conn() as c:
            for row in c.execute("SELECT * FROM substrate_inbound_pos").fetchall():
                payload = dict(row)
                store.cache_record("openboxes", "Inbound Shipment", payload["po_id"], payload, payload["po_id"])
                domains["Inbound Shipment"] = domains.get("Inbound Shipment", 0) + 1
                records_written += 1
            for row in c.execute(
                "SELECT s.sku, s.name, s.category, i.on_hand, s.vendor FROM substrate_skus s "
                "JOIN substrate_inventory i ON i.sku = s.sku"
            ).fetchall():
                payload = dict(row)
                store.cache_record("openboxes", "Product Inventory", payload["sku"], payload, payload["sku"])
                domains["Product Inventory"] = domains.get("Product Inventory", 0) + 1
                records_written += 1
        return IntegrationResult(
            status="success",
            records_read=records_written,
            records_written=records_written,
            summary={"mode": "mock", "domains": domains},
        )

    def _cache(self, domain: str, external_id: str, payload: dict[str, Any], local_id: str | None) -> None:
        store.cache_record(self.definition.system_id, domain, str(external_id), payload, local_id=local_id)
        if local_id:
            store.record_external_ref(
                self.definition.system_id,
                domain,
                str(local_id),
                str(external_id),
                external_url=self._external_url(domain, str(external_id)),
                props={"source": "OpenBoxes"},
            )

    def _external_url(self, domain: str, external_id: str) -> str | None:
        base = os.getenv("OPENBOXES_BASE_URL")
        if not base:
            return None
        path = {
            "Location": "location/show",
            "Product": "product/show",
            "Inbound Shipment": "shipment/show",
        }.get(domain)
        if not path:
            return None
        return f"{base.rstrip('/')}/{path}/{quote(external_id)}"

    def outbound_domain(self, action_type: str) -> str:
        return {"po_held": "Purchase Order", "po_expedited": "Purchase Order", "store_transfer": "Stock Movement"}.get(
            action_type, super().outbound_domain(action_type)
        )

    # ----- live outbound apply --------------------------------------------

    LIVE_ACTION_TYPES = {"po_held", "po_expedited"}

    def apply_outbound(self, action_id: int) -> dict[str, Any]:
        from app.integrations import store as _store

        action = _store.get_outbox_action(action_id, system_id=self.definition.system_id)
        if not action:
            return {"error": f"unknown outbox action: {action_id}"}
        if action["status"] in {"applied", "applied_mock", "draft_created"}:
            return action

        if not self.configured() or action["action_type"] not in self.LIVE_ACTION_TYPES:
            return super().apply_outbound(action_id)

        try:
            outcome = self._dispatch_outbound(action)
        except Exception as exc:  # pragma: no cover - exercised only with live OB
            return _store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": str(exc),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": (
                        "OpenBoxes rejected the apply. The outbox action is "
                        "left in error state — fix the upstream payload "
                        "and retry."
                    ),
                },
            )

        if not outcome.get("external_id"):
            return _store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": outcome.get("message", "OpenBoxes apply produced no external_id"),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": outcome.get("message", "OpenBoxes returned no external_id."),
                    "details": outcome.get("details", {}),
                },
            )

        return _store.update_outbox_action(
            action_id,
            status="draft_created",
            external_id=outcome["external_id"],
            result={
                "message": outcome.get("message", "Comment recorded in OpenBoxes."),
                "system_id": self.definition.system_id,
                "external_domain": action["external_domain"],
                "external_id": outcome["external_id"],
                "details": outcome.get("details", {}),
            },
        )

    def _dispatch_outbound(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = action["action_type"]
        payload = action.get("payload") or {}
        title = action.get("title") or "AI Retail OS action"
        if action_type in ("po_held", "po_expedited"):
            return self._ob_annotate_shipment(title, payload, action_type)
        raise RuntimeError(f"unsupported action_type {action_type!r} for live OpenBoxes apply")

    def _ob_annotate_shipment(
        self,
        title: str,
        payload: dict[str, Any],
        action_type: str,
    ) -> dict[str, Any]:
        """`po_held` / `po_expedited` → POST a Comment on each matching
        inbound shipment.

        OpenBoxes models incoming POs as Shipments with `direction=INBOUND`.
        Our outbox payload carries a list of `pos` rows with at least
        `po_id`. We look those up against the shipments list, post a
        `Comment` on each match, and return the first shipment id as
        the canonical `external_id`.
        """
        po_rows = payload.get("pos") or payload.get("inbound_pos") or []
        if not isinstance(po_rows, list) or not po_rows:
            return {
                "external_id": None,
                "message": (
                    f"{action_type} payload has no `pos` rows; nothing to annotate."
                ),
            }
        # Prefer the inbound-filtered list. Fall back to the unfiltered
        # list only when the filtered request *errors* — a legitimate
        # zero-row inbound result (empty demo) must NOT silently pull
        # outbound shipments and risk annotating the wrong record on a
        # name collision.
        try:
            shipments = self._api_list("shipments", query={"direction": "INBOUND"})
        except Exception:
            shipments = self._api_list("shipments")
        # Index by BOTH `name` and `shipmentNumber` when present. OpenBoxes'
        # Shipment domain populates both fields independently across 0.9.x
        # minors, so keying on only one would silently drop matches when
        # the operator's payload `po_id` lines up with `shipmentNumber`
        # but the shipment also carries a different `name`.
        by_key: dict[str, dict[str, Any]] = {}
        for s in shipments:
            for k in (s.get("name"), s.get("shipmentNumber")):
                if isinstance(k, str) and k.strip():
                    # First write wins for any single key — order from the
                    # API response is preserved by the list iteration.
                    by_key.setdefault(k, s)
        annotated: list[str] = []
        for po in po_rows:
            po_id = po.get("po_id") if isinstance(po, dict) else None
            if not po_id:
                continue
            po_number = po.get("po_number") if isinstance(po, dict) else None
            target = by_key.get(po_id) or (by_key.get(po_number) if po_number else None)
            if target is None:
                continue
            ship_id = _coerce_id(target.get("id"))
            if ship_id is None:
                continue
            self._admin_request(
                f"/api/shipments/{quote(ship_id)}/comments",
                method="POST",
                payload={
                    "comment": (
                        f"[AI Retail OS · {action_type}] {title} — "
                        f"reason: {payload.get('reason') or 'unspecified'}"
                    ),
                },
            )
            annotated.append(ship_id)
        if not annotated:
            return {
                "external_id": None,
                "message": (
                    f"no matching inbound shipments found for {len(po_rows)} payload "
                    f"po_id(s); run `make openboxes-seed` if the demo isn't loaded."
                ),
            }
        return {
            "external_id": annotated[0],
            "message": (
                f"Annotated {len(annotated)} OpenBoxes shipment(s) with the "
                f"{action_type} comment."
            ),
            "details": {"shipment_ids": annotated, "count": len(annotated)},
        }


_AKENEO_MARKER_RE = re.compile(r"\[retail-os:([a-zA-Z0-9_\-]+)\]")


class AkeneoAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="akeneo",
        display_name="Akeneo PIM",
        domain="Product Information",
        docs_url="https://api.akeneo.com/",
        env_keys=("AKENEO_BASE_URL",),
        notes="Product families, categories, attributes, completeness, and merchandising copy.",
    )

    _admin_token: str | None = None

    def configured(self) -> bool:
        if not super().configured():
            return False
        return all(
            os.getenv(k, "").strip()
            for k in ("AKENEO_CLIENT_ID", "AKENEO_SECRET", "AKENEO_USERNAME", "AKENEO_PASSWORD")
        )

    def _login(self) -> str:
        """`POST /api/oauth/v1/token` (password grant) → access_token.

        Akeneo's REST API uses OAuth2 with two layers: the Basic-auth
        client credentials authenticate the calling *application*, and
        the grant_type=password body authenticates the user the call
        runs as. We cache the resulting bearer for the adapter's lifetime
        and `_admin_request` re-logins on 401.
        """
        if self._admin_token:
            return self._admin_token
        basic = b64encode(
            f"{os.environ['AKENEO_CLIENT_ID']}:{os.environ['AKENEO_SECRET']}".encode()
        ).decode()
        # Akeneo's token endpoint expects form-encoded; JsonHttpClient
        # only does JSON. Use urllib directly here.
        body = urlencode(
            {
                "grant_type": "password",
                "username": os.environ["AKENEO_USERNAME"],
                "password": os.environ["AKENEO_PASSWORD"],
            }
        ).encode()
        req = Request(
            os.environ["AKENEO_BASE_URL"].rstrip("/") + "/api/oauth/v1/token",
            data=body,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        token = (data or {}).get("access_token")
        if not token:
            raise RuntimeError(f"akeneo login returned no access_token: {data}")
        self._admin_token = token
        return token

    def _client(self) -> JsonHttpClient:
        token = self._login()
        return JsonHttpClient(
            os.environ["AKENEO_BASE_URL"],
            headers={"Authorization": f"Bearer {token}"},
        )

    def _admin_request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        try:
            return self._client().request(path, method=method, payload=payload)
        except HTTPError as e:
            if e.code != 401:
                raise
            self._admin_token = None
            return self._client().request(path, method=method, payload=payload)

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:  # pragma: no cover - live-system path
            data = self._admin_request("/api/rest/v1/categories?limit=1")
            return IntegrationResult(
                status="connected",
                summary={"categories_seen": len((data.get("_embedded") or {}).get("items") or [])},
            )
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    def sync_inbound(self) -> IntegrationResult:
        if not self.configured():
            return self._mock_sync()
        try:
            return self._live_sync()
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    # ----- live ------------------------------------------------------------

    def _api_list(self, endpoint: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """GET /api/rest/v1/<endpoint> — Akeneo paginates via
        `_links.next`; we walk until exhausted.
        """
        out: list[dict[str, Any]] = []
        next_path: str | None = f"/api/rest/v1/{endpoint}?limit={limit}"
        while next_path:
            data = self._admin_request(next_path)
            items = (data.get("_embedded") or {}).get("items") or []
            if not isinstance(items, list):
                break
            out.extend(items)
            next_link = ((data.get("_links") or {}).get("next") or {}).get("href")
            if not next_link:
                break
            # Akeneo returns absolute URLs; rebuild as a path+query so the
            # client (which already prepends AKENEO_BASE_URL) gets a valid
            # request even when Akeneo's serverURL differs from our env
            # value (proxy / canonical host rewrite). Falls back to the
            # raw string only when urlsplit yields nothing usable.
            parts = urlsplit(next_link)
            if parts.path:
                next_path = parts.path + (f"?{parts.query}" if parts.query else "")
            else:
                next_path = next_link
        return out

    def _live_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_read = 0
        records_written = 0

        # Categories: local_id == code (Akeneo identifies categories
        # by their slug-style code, which the seed sets to substrate
        # `category` directly).
        for cat in self._api_list("categories"):
            external_id = _coerce_id(cat.get("code"))
            if external_id is None:
                continue
            self._cache("Category", external_id, cat, external_id)
            domains["Category"] = domains.get("Category", 0) + 1
            records_written += 1
            records_read += 1

        # Products: Akeneo CE 7+ split the surface into two endpoints —
        # `/api/rest/v1/products` (identifier-based, legacy; *omits*
        # products without an identifier) and `/api/rest/v1/products-uuid`
        # (UUID-keyed, returns every product). Querying only the legacy
        # endpoint silently truncates UUID-only catalogs. We pull both
        # and dedupe on the resolved external_id, falling back to a
        # single endpoint only when the other 404s on older minors.
        seen_ids: set[str] = set()
        product_pages: list[list[dict[str, Any]]] = []
        try:
            product_pages.append(self._api_list("products-uuid"))
        except HTTPError as e:
            if e.code != 404:
                raise
            # Older Akeneo CE — only the identifier endpoint exists.
        try:
            product_pages.append(self._api_list("products"))
        except HTTPError as e:
            if e.code != 404:
                raise
            # Modern Akeneo can theoretically retire `products` later;
            # if it's gone, the products-uuid pull above already covers
            # the catalog.

        for prod in (p for page in product_pages for p in page):
            description = ""
            desc_values = ((prod.get("values") or {}).get("description") or [])
            if isinstance(desc_values, list) and desc_values:
                description = desc_values[0].get("data") or ""
            marker_match = _AKENEO_MARKER_RE.search(description)
            marker_id = marker_match.group(1) if marker_match else None

            external_id = (
                _coerce_id(prod.get("identifier"))
                or _coerce_id(prod.get("uuid"))
                or _coerce_id(marker_id)
            )
            if external_id is None:
                continue
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)
            local_id = prod.get("identifier") or marker_id
            self._cache("Product", external_id, prod, local_id)
            domains["Product"] = domains.get("Product", 0) + 1
            records_written += 1
            records_read += 1

        return IntegrationResult(
            status="success",
            records_read=records_read,
            records_written=records_written,
            summary={"mode": "connected", "domains": domains},
        )

    # ----- mock ------------------------------------------------------------

    def _mock_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_written = 0
        with conn() as c:
            for row in c.execute("SELECT * FROM substrate_categories").fetchall():
                payload = dict(row)
                payload["pim_completeness"] = 0.92 if payload["category"] != "electronics" else 0.74
                store.cache_record("akeneo", "Category", payload["category"], payload, payload["category"])
                domains["Category"] = domains.get("Category", 0) + 1
                records_written += 1
            for row in c.execute("SELECT * FROM substrate_skus").fetchall():
                payload = dict(row)
                payload["pim_completeness"] = 0.9
                store.cache_record("akeneo", "Product", payload["sku"], payload, payload["sku"])
                domains["Product"] = domains.get("Product", 0) + 1
                records_written += 1
        return IntegrationResult(
            status="success",
            records_read=records_written,
            records_written=records_written,
            summary={"mode": "mock", "domains": domains},
        )

    def _cache(self, domain: str, external_id: str, payload: dict[str, Any], local_id: str | None) -> None:
        store.cache_record(self.definition.system_id, domain, str(external_id), payload, local_id=local_id)
        if local_id:
            store.record_external_ref(
                self.definition.system_id,
                domain,
                str(local_id),
                str(external_id),
                external_url=self._external_url(domain, str(external_id)),
                props={"source": "Akeneo"},
            )

    def _external_url(self, domain: str, external_id: str) -> str | None:
        base = os.getenv("AKENEO_BASE_URL")
        if not base:
            return None
        path = {
            "Category": "#/configuration/category/tree",
            "Product": "#/enrich/product",
        }.get(domain)
        if not path:
            return None
        return f"{base.rstrip('/')}/{path}/{quote(external_id)}"

    # ----- live outbound apply --------------------------------------------

    LIVE_ACTION_TYPES = {"pim_enrich"}  # extension point for the cockpit
    # `pim_enrich` is currently emitted by no agent — Akeneo's role in
    # the demo is read-only product-information enrichment surfaced into
    # the cockpit. The dispatcher is wired so a future Merchandiser
    # agent that proposes copy / metadata edits can apply via PATCH
    # /api/rest/v1/products/{code} without further adapter work.

    def apply_outbound(self, action_id: int) -> dict[str, Any]:
        from app.integrations import store as _store

        action = _store.get_outbox_action(action_id, system_id=self.definition.system_id)
        if not action:
            return {"error": f"unknown outbox action: {action_id}"}
        if action["status"] in {"applied", "applied_mock", "draft_created"}:
            return action
        if not self.configured() or action["action_type"] not in self.LIVE_ACTION_TYPES:
            return super().apply_outbound(action_id)
        try:
            outcome = self._dispatch_outbound(action)
        except Exception as exc:  # pragma: no cover - live only
            return _store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": str(exc),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": "Akeneo rejected the apply.",
                },
            )
        if not outcome.get("external_id"):
            return _store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": outcome.get("message", "Akeneo apply produced no external_id"),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": outcome.get("message", "Akeneo returned no external_id."),
                    "details": outcome.get("details", {}),
                },
            )
        return _store.update_outbox_action(
            action_id,
            status="draft_created",
            external_id=outcome["external_id"],
            result={
                "message": outcome.get("message", "PIM enrichment applied."),
                "system_id": self.definition.system_id,
                "external_domain": action["external_domain"],
                "external_id": outcome["external_id"],
                "details": outcome.get("details", {}),
            },
        )

    def _dispatch_outbound(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = action["action_type"]
        payload = action.get("payload") or {}
        if action_type == "pim_enrich":
            return self._akeneo_enrich_product(payload)
        raise RuntimeError(f"unsupported action_type {action_type!r} for live Akeneo apply")

    def _akeneo_enrich_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        """`pim_enrich` → PATCH /api/rest/v1/products/{sku} with the
        operator-supplied `values` block (and optionally `categories`).
        Akeneo's PATCH semantics merge — fields not in the body are
        untouched.
        """
        sku = payload.get("sku") or payload.get("identifier")
        if not sku:
            return {
                "external_id": None,
                "message": "pim_enrich payload has no sku/identifier.",
            }
        body: dict[str, Any] = {"identifier": sku}
        if payload.get("values"):
            body["values"] = payload["values"]
        if payload.get("categories"):
            body["categories"] = payload["categories"]
        if "enabled" in payload:
            body["enabled"] = payload["enabled"]
        self._admin_request(f"/api/rest/v1/products/{quote(sku)}", method="PATCH", payload=body)
        return {
            "external_id": sku,
            "message": f"Akeneo product {sku} enriched.",
            "details": {"sku": sku, "fields": list((body.get("values") or {}).keys())},
        }


class SupersetAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="superset",
        display_name="Apache Superset",
        domain="BI / Analytics",
        docs_url="https://superset.apache.org/",
        env_keys=("SUPERSET_BASE_URL",),
        notes="External BI dashboards over the retail spine; SQLite now, optional Postgres later.",
    )

    _admin_token: str | None = None

    def configured(self) -> bool:
        if not super().configured():
            return False
        return all(os.getenv(k, "").strip() for k in ("SUPERSET_USERNAME", "SUPERSET_PASSWORD"))

    def _login(self) -> str:
        """`POST /api/v1/security/login` — Flask-AppBuilder JWT.

        Superset's API uses a short-lived JWT minted by the FAB security
        layer. We cache the bearer for the adapter's lifetime; `_admin_request`
        re-logins on 401 (mirror of Akeneo / Medusa / OpenBoxes).
        """
        if self._admin_token:
            return self._admin_token
        client = JsonHttpClient(os.environ["SUPERSET_BASE_URL"])
        data = client.request(
            "/api/v1/security/login",
            method="POST",
            payload={
                "username": os.environ["SUPERSET_USERNAME"],
                "password": os.environ["SUPERSET_PASSWORD"],
                "provider": "db",
                "refresh": True,
            },
        )
        token = (data or {}).get("access_token")
        if not token:
            raise RuntimeError(f"superset login returned no access_token: {data}")
        self._admin_token = token
        return token

    def _client(self) -> JsonHttpClient:
        token = self._login()
        return JsonHttpClient(
            os.environ["SUPERSET_BASE_URL"],
            headers={"Authorization": f"Bearer {token}"},
        )

    def _admin_request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        try:
            return self._client().request(path, method=method, payload=payload)
        except HTTPError as e:
            if e.code != 401:
                raise
            self._admin_token = None
            return self._client().request(path, method=method, payload=payload)

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:  # pragma: no cover - live-system path
            data = self._admin_request("/api/v1/dashboard/?q=(page_size:1)")
            return IntegrationResult(
                status="connected",
                summary={"dashboards_seen": int((data or {}).get("count", 0) or 0)},
            )
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    def sync_inbound(self) -> IntegrationResult:
        if not self.configured():
            return self._mock_sync()
        try:
            return self._live_sync()
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    # ----- live ------------------------------------------------------------

    def _api_list(self, endpoint: str, *, page_size: int = 100) -> list[dict[str, Any]]:
        """GET /api/v1/<endpoint>?q=(page:N,page_size:M) — Superset's
        v1 list endpoints paginate via `page` / `page_size`. We walk
        until `count` is consumed.
        """
        out: list[dict[str, Any]] = []
        page = 0
        while True:
            qs = f"q=(page:{page},page_size:{page_size})"
            data = self._admin_request(f"{endpoint}?{qs}") or {}
            items = data.get("result") or []
            if not isinstance(items, list):
                break
            out.extend(items)
            count = int(data.get("count", 0) or 0)
            if not items or len(out) >= count:
                break
            page += 1
            if page > 200:  # absolute belt-and-suspenders cap
                break
        return out

    def _live_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_read = 0
        records_written = 0

        # Databases — the operator-registered SQLAlchemy connections.
        # `AI Retail OS spine` (the seed leaves this name) round-trips
        # cleanly so the cockpit can drill from a dashboard back to a
        # known spine.db source.
        for db in self._api_list("/api/v1/database/"):
            external_id = _coerce_id(db.get("id"))
            if external_id is None:
                continue
            local_id = db.get("database_name")
            self._cache("Database", external_id, db, local_id)
            domains["Database"] = domains.get("Database", 0) + 1
            records_written += 1
            records_read += 1

        # Datasets — Superset's term for a registered table/view.
        for ds in self._api_list("/api/v1/dataset/"):
            external_id = _coerce_id(ds.get("id"))
            if external_id is None:
                continue
            # local_id := the underlying table_name (the seed registers
            # `substrate_*` tables, which aligns with substrate names).
            local_id = ds.get("table_name")
            self._cache("Dataset", external_id, ds, local_id)
            domains["Dataset"] = domains.get("Dataset", 0) + 1
            records_written += 1
            records_read += 1

        # Charts ("slices") — keyed by `slice_name` so the seed's
        # stable names round-trip into the spine.
        for chart in self._api_list("/api/v1/chart/"):
            external_id = _coerce_id(chart.get("id"))
            if external_id is None:
                continue
            local_id = chart.get("slice_name")
            self._cache("Chart", external_id, chart, local_id)
            domains["Chart"] = domains.get("Chart", 0) + 1
            records_written += 1
            records_read += 1

        # Dashboards — the operator-facing surface. local_id prefers
        # the slug (stable) over the title.
        for dash in self._api_list("/api/v1/dashboard/"):
            external_id = _coerce_id(dash.get("id"))
            if external_id is None:
                continue
            local_id = dash.get("slug") or dash.get("dashboard_title")
            self._cache("Dashboard", external_id, dash, local_id)
            domains["Dashboard"] = domains.get("Dashboard", 0) + 1
            records_written += 1
            records_read += 1

        return IntegrationResult(
            status="success",
            records_read=records_read,
            records_written=records_written,
            summary={"mode": "connected", "domains": domains},
        )

    # ----- mock ------------------------------------------------------------

    def _mock_sync(self) -> IntegrationResult:
        base = os.getenv("SUPERSET_BASE_URL", "").rstrip("/")
        dashboards = [
            {
                "id": "retail-inventory-health",
                "title": "Inventory Health",
                "source": "AI Retail OS spine",
                "url": f"{base}/superset/dashboard/retail-inventory-health" if base else None,
            },
            {
                "id": "campaign-roi",
                "title": "Campaign ROI",
                "source": "AI Retail OS spine",
                "url": f"{base}/superset/dashboard/campaign-roi" if base else None,
            },
        ]
        for dashboard in dashboards:
            store.cache_record("superset", "Dashboard", dashboard["id"], dashboard, dashboard["id"])
            store.record_external_ref(
                "superset",
                "Dashboard",
                dashboard["id"],
                dashboard["id"],
                dashboard.get("url"),
            )
        return IntegrationResult(
            status="success",
            records_read=len(dashboards),
            records_written=len(dashboards),
            summary={"mode": "mock", "domains": {"Dashboard": len(dashboards)}},
        )

    def _cache(self, domain: str, external_id: str, payload: dict[str, Any], local_id: Any) -> None:
        local = _coerce_id(local_id) if local_id is not None else None
        store.cache_record(self.definition.system_id, domain, str(external_id), payload, local_id=local)
        if local:
            store.record_external_ref(
                self.definition.system_id,
                domain,
                local,
                str(external_id),
                external_url=self._external_url(domain, str(external_id)),
                props={"source": "Superset"},
            )

    def _external_url(self, domain: str, external_id: str) -> str | None:
        base = os.getenv("SUPERSET_BASE_URL")
        if not base:
            return None
        path = {
            "Dashboard": "superset/dashboard",
            "Chart": "explore/?slice_id",
            "Dataset": "tablemodelview/edit",
            "Database": "databaseview/edit",
        }.get(domain)
        if not path:
            return None
        if domain == "Chart":
            return f"{base.rstrip('/')}/{path}={quote(external_id)}"
        return f"{base.rstrip('/')}/{path}/{quote(external_id)}"

    # ----- live outbound apply --------------------------------------------
    #
    # Superset is read-only in our architecture: no agent emits an
    # action_type that mutates Superset state. We rely on the
    # IntegrationAdapter base class — `apply_outbound` returns
    # `applied_mock` (no creds) or `draft_created` (creds present)
    # without making any HTTP call. Override is intentionally absent.
    LIVE_ACTION_TYPES: set[str] = set()


def _shopify_gid_tail(gid: str | None) -> str | None:
    """`gid://shopify/Product/12345` → `12345`. Tolerates already-numeric ids."""
    if not gid:
        return None
    if "/" not in str(gid):
        return str(gid)
    tail = str(gid).rsplit("/", 1)[-1]
    return tail or None


class ShopifyAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="shopify",
        display_name="Shopify Plus",
        domain="Ecommerce / OMS / Marketing",
        docs_url="https://shopify.dev/docs/api/admin-graphql",
        env_keys=("SHOPIFY_SHOP_DOMAIN", "SHOPIFY_ADMIN_TOKEN"),
        notes=(
            "Shopify Plus dev-store adapter — Admin GraphQL for sync, "
            "Discounts / Fulfillment / Marketing mutations for outbound apply."
        ),
    )

    DEFAULT_API_VERSION = "2025-01"

    def _api_version(self) -> str:
        return os.environ.get("SHOPIFY_API_VERSION", self.DEFAULT_API_VERSION).strip() or self.DEFAULT_API_VERSION

    def _client(self) -> JsonHttpClient:
        domain = os.environ["SHOPIFY_SHOP_DOMAIN"].strip()
        return JsonHttpClient(
            f"https://{domain}",
            headers={
                "X-Shopify-Access-Token": os.environ["SHOPIFY_ADMIN_TOKEN"].strip(),
            },
        )

    # ----- GraphQL helpers -------------------------------------------------

    def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST a GraphQL operation. Raises on top-level `errors` payload."""
        body = {"query": query, "variables": variables or {}}
        path = f"/admin/api/{self._api_version()}/graphql.json"
        data = self._client().request(path, method="POST", payload=body)
        if isinstance(data, dict) and data.get("errors"):
            raise RuntimeError(f"shopify graphql errors: {json.dumps(data['errors'])}")
        return (data or {}).get("data") or {}

    def _gql_list(
        self,
        query: str,
        root_key: str,
        variables: dict[str, Any] | None = None,
        max_rows: int | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Walk a Shopify connection via `pageInfo.endCursor` until exhausted.

        Mirrors Mautic / Medusa: max_rows=None means uncapped (default through
        `_live_sync`); an explicit cap that gets hit returns truncated=True.
        """
        out: list[dict[str, Any]] = []
        truncated = False
        cursor: str | None = None
        params = dict(variables or {})
        while True:
            params["cursor"] = cursor
            data = self._gql(query, params)
            conn_obj = data.get(root_key) or {}
            edges = conn_obj.get("edges") or []
            for edge in edges:
                node = edge.get("node")
                if isinstance(node, dict):
                    out.append(node)
                if max_rows is not None and len(out) >= max_rows:
                    page_info = conn_obj.get("pageInfo") or {}
                    truncated = bool(page_info.get("hasNextPage"))
                    return out, truncated
            page_info = conn_obj.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break
        return out, truncated

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:  # pragma: no cover - live-system path
            data = self._gql("query Shop { shop { name myshopifyDomain } }")
            shop = (data.get("shop") or {})
            return IntegrationResult(
                status="connected",
                summary={"shop": shop.get("name"), "domain": shop.get("myshopifyDomain")},
            )
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    def sync_inbound(self) -> IntegrationResult:
        if not self.configured():
            return self._mock_sync()
        try:
            return self._live_sync()
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    # ----- live ------------------------------------------------------------

    PRODUCTS_Q = """
    query ShopifyProducts($cursor: String) {
      products(first: 100, after: $cursor) {
        edges {
          node {
            id
            title
            vendor
            productType
            tags
            metafield(namespace: "retail_os", key: "spine_sku") { value }
            variants(first: 1) {
              edges { node { id sku price inventoryItem { id } } }
            }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """

    LOCATIONS_Q = """
    query ShopifyLocations($cursor: String) {
      locations(first: 100, after: $cursor) {
        edges {
          node {
            id
            name
            address { city country }
            metafield(namespace: "retail_os", key: "store_id") { value }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """

    INVENTORY_Q = """
    query ShopifyInventory($cursor: String) {
      inventoryItems(first: 100, after: $cursor) {
        edges {
          node {
            id
            sku
            inventoryLevels(first: 100) {
              edges {
                node {
                  location { id name }
                  quantities(names: ["available"]) { name quantity }
                }
              }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """

    INVENTORY_LEVELS_Q = """
    query ShopifyInventoryLevels($id: ID!, $cursor: String) {
      inventoryItem(id: $id) {
        inventoryLevels(first: 100, after: $cursor) {
          edges {
            node {
              location { id name }
              quantities(names: ["available"]) { name quantity }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """

    ORDERS_Q = """
    query ShopifyOrders($cursor: String) {
      orders(first: 100, after: $cursor, sortKey: CREATED_AT, reverse: true) {
        edges {
          node {
            id
            name
            createdAt
            displayFinancialStatus
            displayFulfillmentStatus
            currentTotalPriceSet { shopMoney { amount currencyCode } }
            tags
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """

    def _live_sync(self, max_rows: int | None = None) -> IntegrationResult:
        domains: dict[str, int] = {}
        truncated_domains: list[str] = []
        records_read = 0
        records_written = 0

        # Locations first — products + inventory deep-links rely on them.
        locations, loc_trunc = self._gql_list(self.LOCATIONS_Q, "locations", max_rows=max_rows)
        for loc in locations:
            external_id = _shopify_gid_tail(loc.get("id"))
            if not external_id:
                continue
            mf = loc.get("metafield") or {}
            local_id = mf.get("value") if isinstance(mf, dict) else None
            self._cache("Location", external_id, loc, local_id)
            domains["Location"] = domains.get("Location", 0) + 1
            records_written += 1
            records_read += 1
        if loc_trunc:
            truncated_domains.append("Location")

        # Products. local_id = variant SKU (preferred) or metafield fallback.
        products, prod_trunc = self._gql_list(self.PRODUCTS_Q, "products", max_rows=max_rows)
        for prod in products:
            external_id = _shopify_gid_tail(prod.get("id"))
            if not external_id:
                continue
            sku = None
            variant_edges = ((prod.get("variants") or {}).get("edges") or [])
            if variant_edges:
                sku = (variant_edges[0].get("node") or {}).get("sku")
            if not sku:
                mf = prod.get("metafield") or {}
                sku = mf.get("value") if isinstance(mf, dict) else None
            self._cache("Product", external_id, prod, sku)
            domains["Product"] = domains.get("Product", 0) + 1
            records_written += 1
            records_read += 1
        if prod_trunc:
            truncated_domains.append("Product")

        # Inventory levels. external_id is a synthetic <inventory_item>:<location>
        # pair; local_id is the SKU so the cockpit drawer can drill from a
        # substrate SKU to every per-location row.
        inv_items, inv_trunc = self._gql_list(self.INVENTORY_Q, "inventoryItems", max_rows=max_rows)

        def _ingest_level_node(node: dict[str, Any], item_id: str, sku: str | None) -> None:
            nonlocal records_written, records_read
            loc_id = _shopify_gid_tail(((node.get("location") or {}).get("id")))
            qty_rows = node.get("quantities") or []
            qty = next(
                (q.get("quantity") for q in qty_rows if (q or {}).get("name") == "available"),
                None,
            )
            external_id = f"{item_id}:{loc_id}"
            payload = {"inventory_item_id": item_id, "location_id": loc_id, "available": qty, "sku": sku}
            self._cache("Inventory Level", external_id, payload, sku)
            domains["Inventory Level"] = domains.get("Inventory Level", 0) + 1
            records_written += 1
            records_read += 1

        for item in inv_items:
            sku = item.get("sku")
            item_id = _shopify_gid_tail(item.get("id"))
            if not item_id:
                continue
            levels_conn = item.get("inventoryLevels") or {}
            for edge in levels_conn.get("edges") or []:
                node = edge.get("node") or {}
                _ingest_level_node(node, item_id, sku)
            # Walk additional pages for items stocked in >100 locations.
            page_info = levels_conn.get("pageInfo") or {}
            cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
            while cursor:
                data = self._gql(
                    self.INVENTORY_LEVELS_Q,
                    {"id": item.get("id"), "cursor": cursor},
                )
                next_conn = ((data or {}).get("inventoryItem") or {}).get("inventoryLevels") or {}
                for edge in next_conn.get("edges") or []:
                    node = edge.get("node") or {}
                    _ingest_level_node(node, item_id, sku)
                next_page = next_conn.get("pageInfo") or {}
                cursor = next_page.get("endCursor") if next_page.get("hasNextPage") else None
        if inv_trunc:
            truncated_domains.append("Inventory Level")

        # Orders — local_id stays None (no clean substrate round-trip key).
        orders, ord_trunc = self._gql_list(self.ORDERS_Q, "orders", max_rows=max_rows)
        for order in orders:
            external_id = _shopify_gid_tail(order.get("id"))
            if not external_id:
                continue
            self._cache("Order", external_id, order, None)
            domains["Order"] = domains.get("Order", 0) + 1
            records_written += 1
            records_read += 1
        if ord_trunc:
            truncated_domains.append("Order")

        summary: dict[str, Any] = {"mode": "connected", "domains": domains}
        if truncated_domains:
            summary["truncated_domains"] = truncated_domains
        status = "partial" if truncated_domains else "success"
        return IntegrationResult(
            status=status,
            records_read=records_read,
            records_written=records_written,
            summary=summary,
        )

    # ----- mock ------------------------------------------------------------

    def _mock_sync(self) -> IntegrationResult:
        domains: dict[str, int] = {}
        records_written = 0
        with conn() as c:
            for row in c.execute("SELECT * FROM substrate_skus ORDER BY sku").fetchall():
                payload = dict(row)
                store.cache_record("shopify", "Product", payload["sku"], payload, payload["sku"])
                domains["Product"] = domains.get("Product", 0) + 1
                records_written += 1
            for row in c.execute("SELECT * FROM substrate_stores ORDER BY store_id").fetchall():
                payload = dict(row)
                store.cache_record(
                    "shopify", "Location", payload["store_id"], payload, payload["store_id"]
                )
                domains["Location"] = domains.get("Location", 0) + 1
                records_written += 1
            for row in c.execute(
                "SELECT store_id, sku, on_hand FROM substrate_store_inventory ORDER BY store_id, sku LIMIT 200"
            ).fetchall():
                payload = dict(row)
                external_id = f"{payload['store_id']}:{payload['sku']}"
                store.cache_record("shopify", "Inventory Level", external_id, payload, payload["sku"])
                domains["Inventory Level"] = domains.get("Inventory Level", 0) + 1
                records_written += 1
            for row in c.execute(
                "SELECT * FROM substrate_orders ORDER BY id DESC LIMIT 50"
            ).fetchall():
                payload = dict(row)
                store.cache_record("shopify", "Order", str(payload["id"]), payload, None)
                domains["Order"] = domains.get("Order", 0) + 1
                records_written += 1
        return IntegrationResult(
            status="success",
            records_read=records_written,
            records_written=records_written,
            summary={"mode": "mock", "domains": domains},
        )

    def _cache(self, domain: str, external_id: str, payload: dict[str, Any], local_id: str | None) -> None:
        store.cache_record(self.definition.system_id, domain, str(external_id), payload, local_id=local_id)
        if local_id:
            store.record_external_ref(
                self.definition.system_id,
                domain,
                str(local_id),
                str(external_id),
                external_url=self._external_url(domain, str(external_id)),
                props={"source": "Shopify"},
            )

    def _external_url(self, domain: str, external_id: str) -> str | None:
        shop_domain = os.getenv("SHOPIFY_SHOP_DOMAIN", "").strip()
        if not shop_domain:
            return None
        # Inventory Level external_id is "<item>:<location>" — link to the
        # location's inventory page since per-item URLs aren't first-class.
        if domain == "Inventory Level":
            tail = external_id.split(":", 1)[-1]
            return f"https://{shop_domain}/admin/settings/locations/{quote(tail)}"
        path = {
            "Product": "admin/products",
            "Location": "admin/settings/locations",
            "Order": "admin/orders",
        }.get(domain)
        if not path:
            return None
        return f"https://{shop_domain}/{path}/{quote(external_id)}"

    def outbound_domain(self, action_type: str) -> str:
        return {
            "promotion": "Discount",
            "fulfillment_routing": "Fulfillment",
            "campaign_brief": "Campaign",
        }.get(action_type, super().outbound_domain(action_type))

    # ----- live outbound apply --------------------------------------------

    LIVE_ACTION_TYPES = {"promotion", "fulfillment_routing", "campaign_brief"}

    def apply_outbound(self, action_id: int) -> dict[str, Any]:
        action = store.get_outbox_action(action_id, system_id=self.definition.system_id)
        if not action:
            return {"error": f"unknown outbox action: {action_id}"}
        if action["status"] in {"applied", "applied_mock", "draft_created"}:
            return action

        if not self.configured() or action["action_type"] not in self.LIVE_ACTION_TYPES:
            return super().apply_outbound(action_id)

        try:
            outcome = self._dispatch_outbound(action)
        except Exception as exc:  # pragma: no cover - exercised only with live Shopify
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": str(exc),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": (
                        "Shopify rejected the apply. The outbox action is left "
                        "in error state — fix the upstream payload and retry."
                    ),
                },
            )

        if not outcome.get("external_id"):
            return store.update_outbox_action(
                action_id,
                status="error",
                result={
                    "error": outcome.get("message", "Shopify apply produced no external_id"),
                    "system_id": self.definition.system_id,
                    "external_domain": action["external_domain"],
                    "message": outcome.get(
                        "message",
                        "Shopify returned no external_id — nothing was written. Check the outbox payload and retry.",
                    ),
                    "details": outcome.get("details", {}),
                },
            )

        return store.update_outbox_action(
            action_id,
            status="draft_created",
            external_id=outcome["external_id"],
            result={
                "message": outcome.get("message", "Recorded action in Shopify."),
                "system_id": self.definition.system_id,
                "external_domain": action["external_domain"],
                "external_id": outcome["external_id"],
                "details": outcome.get("details", {}),
            },
        )

    def _dispatch_outbound(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = action["action_type"]
        payload = action.get("payload") or {}
        title = action.get("title") or "AI Retail OS action"
        if action_type == "promotion":
            return self._shopify_create_discount(title, payload)
        if action_type == "fulfillment_routing":
            return self._shopify_route_fulfillment(title, payload)
        if action_type == "campaign_brief":
            return self._shopify_campaign_brief(title, payload)
        raise RuntimeError(f"unsupported action_type {action_type!r} for live Shopify apply")

    @staticmethod
    def _payload_marker(payload: dict[str, Any]) -> str:
        """sha256-over-sorted-keys marker for idempotent metafield appends."""
        import hashlib

        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return f"p{digest[:12]}"

    DISCOUNT_AUTOMATIC_CREATE = """
    mutation DiscountAutomaticBasicCreate($automaticBasicDiscount: DiscountAutomaticBasicInput!) {
      discountAutomaticBasicCreate(automaticBasicDiscount: $automaticBasicDiscount) {
        automaticDiscountNode { id }
        userErrors { field message }
      }
    }
    """

    def _shopify_create_discount(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a percentage-off automatic discount targeted at all items.

        Category-scoped collections aren't created by the seed, so the demo
        path lands a store-wide percent-off discount with the category
        embedded in the title for audit. Operator can scope it manually if
        needed — the cockpit's job is to draft, not to enforce scope.
        """
        from datetime import datetime, timedelta, timezone

        pct = float(payload.get("discount_pct") or payload.get("percentage") or 0.20)
        if pct > 1:
            pct = pct / 100.0
        starts = datetime.now(timezone.utc).replace(microsecond=0)
        ends = starts + timedelta(days=30)
        discount_title = f"AI Retail OS — {title} ({payload.get('category', 'all')})"
        result = self._gql(
            self.DISCOUNT_AUTOMATIC_CREATE,
            {
                "automaticBasicDiscount": {
                    "title": discount_title,
                    "startsAt": starts.isoformat().replace("+00:00", "Z"),
                    "endsAt": ends.isoformat().replace("+00:00", "Z"),
                    "minimumRequirement": {"subtotal": {"greaterThanOrEqualToSubtotal": "0.00"}},
                    "customerGets": {
                        "value": {"percentage": round(pct, 4)},
                        "items": {"all": True},
                    },
                    "customerSelection": {"all": True},
                }
            },
        )
        op = result.get("discountAutomaticBasicCreate") or {}
        errs = op.get("userErrors") or []
        if errs:
            raise RuntimeError(f"discountAutomaticBasicCreate userErrors: {json.dumps(errs)}")
        node = op.get("automaticDiscountNode") or {}
        gid = node.get("id")
        external_id = _shopify_gid_tail(gid)
        return {
            "external_id": external_id,
            "message": f"Created Shopify automatic discount {discount_title!r} ({pct * 100:.1f}% off, 30-day).",
            "details": {"discount_node_id": gid, "discount_pct": pct},
        }

    METAFIELDS_SET_MUT = """
    mutation MetafieldsSet($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id namespace key value }
        userErrors { field message }
      }
    }
    """

    LOCATION_BY_STORE_ID = """
    query LocationByStoreId($cursor: String) {
      locations(first: 100, after: $cursor) {
        edges {
          node {
            id
            metafield(namespace: "retail_os", key: "store_id") { value }
            routingMf: metafield(namespace: "retail_os", key: "routing_log") { value }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """

    def _shopify_find_location(self, store_id: str) -> tuple[str, str | None] | None:
        """Return (location_gid, current_routing_log_json) for a store_id, or None."""
        cursor: str | None = None
        while True:
            data = self._gql(self.LOCATION_BY_STORE_ID, {"cursor": cursor})
            conn_obj = data.get("locations") or {}
            for edge in conn_obj.get("edges") or []:
                node = edge.get("node") or {}
                mf = node.get("metafield") or {}
                if isinstance(mf, dict) and mf.get("value") == store_id:
                    routing = (node.get("routingMf") or {}).get("value")
                    return node.get("id"), routing
            page_info = conn_obj.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return None
            cursor = page_info.get("endCursor")
            if not cursor:
                return None

    def _shopify_route_fulfillment(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Stash the routing recommendation as a metafield on the named
        location. Shopify's `fulfillmentOrderMove` needs a concrete order id
        which the cockpit payloads don't carry; metadata-stash mirrors the
        Medusa P4 shape (auditable, idempotent, no fake orders).
        """
        target = (payload.get("recommended_store") or payload.get("to_store") or "").strip()
        if not target:
            return {
                "external_id": None,
                "message": (
                    "fulfillment_routing payload missing `recommended_store` (or `to_store`); "
                    "can't pick a Shopify location to record the routing intent."
                ),
            }
        found = self._shopify_find_location(target)
        if not found:
            return {
                "external_id": None,
                "message": (
                    f"no Shopify location with retail_os.store_id={target!r}; "
                    "run `make shopify-seed` if the demo isn't loaded."
                ),
            }
        loc_gid, existing_log_json = found
        marker = self._payload_marker({"target": target, "title": title, "payload": payload})
        try:
            log = json.loads(existing_log_json) if existing_log_json else []
            if not isinstance(log, list):
                log = []
        except Exception:
            log = []
        if any(isinstance(e, dict) and e.get("marker") == marker for e in log):
            return {
                "external_id": _shopify_gid_tail(loc_gid),
                "message": f"Routing intent already recorded on location {target}; reused.",
                "details": {"location_id": loc_gid, "marker": marker, "reused": True},
            }
        log.append({"marker": marker, "title": title, "payload": payload})
        result = self._gql(
            self.METAFIELDS_SET_MUT,
            {
                "metafields": [
                    {
                        "ownerId": loc_gid,
                        "namespace": "retail_os",
                        "key": "routing_log",
                        "type": "json",
                        "value": json.dumps(log),
                    }
                ]
            },
        )
        op = result.get("metafieldsSet") or {}
        errs = op.get("userErrors") or []
        if errs:
            raise RuntimeError(f"metafieldsSet (location routing) userErrors: {json.dumps(errs)}")
        return {
            "external_id": _shopify_gid_tail(loc_gid),
            "message": f"Recorded routing intent on Shopify location {target}.",
            "details": {"location_id": loc_gid, "marker": marker},
        }

    SHOP_ID_Q = "query ShopId { shop { id } }"

    def _shop_gid(self) -> str:
        data = self._gql(self.SHOP_ID_Q)
        gid = ((data.get("shop") or {}).get("id"))
        if not gid:
            raise RuntimeError("Shopify Shop query returned no id")
        return gid

    def _shopify_campaign_brief(self, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Klaviyo passthrough when configured; otherwise stash the brief
        as a metafield on the Shop entity (auditable, idempotent).
        """
        marker = self._payload_marker({"title": title, "payload": payload})

        klaviyo_key = os.getenv("KLAVIYO_API_KEY", "").strip()
        if klaviyo_key:
            return self._klaviyo_create_campaign(title, payload, klaviyo_key, marker)

        # Shop-level metafield stash
        shop_gid = self._shop_gid()
        # Read existing campaign_briefs list
        existing = self._gql(
            """
            query ShopBriefs { shop { id metafield(namespace: "retail_os", key: "campaign_briefs") { value } } }
            """
        )
        prior_value = (((existing.get("shop") or {}).get("metafield") or {}) or {}).get("value")
        try:
            briefs = json.loads(prior_value) if prior_value else []
            if not isinstance(briefs, list):
                briefs = []
        except Exception:
            briefs = []
        if any(isinstance(e, dict) and e.get("marker") == marker for e in briefs):
            return {
                "external_id": marker,
                "message": "Campaign brief already recorded on Shop metafield; reused.",
                "details": {"shop_id": shop_gid, "marker": marker, "reused": True, "channel": "shopify-shop-metafield"},
            }
        briefs.append({"marker": marker, "title": title, "payload": payload})
        result = self._gql(
            self.METAFIELDS_SET_MUT,
            {
                "metafields": [
                    {
                        "ownerId": shop_gid,
                        "namespace": "retail_os",
                        "key": "campaign_briefs",
                        "type": "json",
                        "value": json.dumps(briefs),
                    }
                ]
            },
        )
        op = result.get("metafieldsSet") or {}
        errs = op.get("userErrors") or []
        if errs:
            raise RuntimeError(f"metafieldsSet (campaign_brief) userErrors: {json.dumps(errs)}")
        return {
            "external_id": marker,
            "message": f"Recorded campaign brief {title!r} on Shop metafield retail_os.campaign_briefs.",
            "details": {"shop_id": shop_gid, "marker": marker, "channel": "shopify-shop-metafield"},
        }

    def _klaviyo_create_campaign(
        self, title: str, payload: dict[str, Any], api_key: str, marker: str
    ) -> dict[str, Any]:
        """POST a draft campaign to Klaviyo. We don't deduplicate via Klaviyo's
        list endpoint to keep the call shape minimal — the cockpit's outbox
        status guards against double-application via the action_id.
        """
        body = {
            "data": {
                "type": "campaign",
                "attributes": {
                    "name": f"AI Retail OS — {title} [{marker}]",
                    "channel": "email",
                    "audiences": {"included": [], "excluded": []},
                    "send_strategy": {"method": "static"},
                },
            }
        }
        client = JsonHttpClient(
            "https://a.klaviyo.com",
            headers={
                "Authorization": f"Klaviyo-API-Key {api_key}",
                "revision": "2024-10-15",
            },
        )
        data = client.request("/api/campaigns/", method="POST", payload=body)
        camp_id = ((data.get("data") or {}).get("id"))
        if not camp_id:
            return {
                "external_id": None,
                "message": f"Klaviyo /api/campaigns/ returned no id: {data!r}",
            }
        return {
            "external_id": camp_id,
            "message": f"Created Klaviyo draft campaign {camp_id} for {title!r}.",
            "details": {"klaviyo_campaign_id": camp_id, "marker": marker, "channel": "klaviyo"},
        }


ADAPTERS: list[IntegrationAdapter] = [
    ERPNextAdapter(),
    MauticAdapter(),
    MedusaAdapter(),
    OpenBoxesAdapter(),
    AkeneoAdapter(),
    SupersetAdapter(),
    ShopifyAdapter(),
]
