from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timezone
import json
import os
import re
from typing import Any
from urllib.parse import quote, urlencode
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

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:  # pragma: no cover - live-system path
            data = self._client().request("/admin/products?limit=1")
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
            data = self._client().request(f"/admin/{endpoint}?{qs}")
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


class OpenBoxesAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="openboxes",
        display_name="OpenBoxes",
        domain="Warehouse / Supply Chain",
        docs_url="https://docs.openboxes.com/en/latest/",
        env_keys=("OPENBOXES_BASE_URL",),
        notes="Warehouse/DC inventory, receiving, stock movements, and inbound supplier realism.",
    )

    def sync_inbound(self) -> IntegrationResult:
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
            summary={"mode": "mock" if not self.configured() else "connected_stub", "domains": domains},
        )

    def outbound_domain(self, action_type: str) -> str:
        return {"po_held": "Purchase Order", "po_expedited": "Purchase Order", "store_transfer": "Stock Movement"}.get(
            action_type, super().outbound_domain(action_type)
        )


class AkeneoAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="akeneo",
        display_name="Akeneo PIM",
        domain="Product Information",
        docs_url="https://api.akeneo.com/",
        env_keys=("AKENEO_BASE_URL",),
        notes="Product families, categories, attributes, completeness, and merchandising copy.",
    )

    def sync_inbound(self) -> IntegrationResult:
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
            summary={"mode": "mock" if not self.configured() else "connected_stub", "domains": domains},
        )


class SupersetAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="superset",
        display_name="Apache Superset",
        domain="BI / Analytics",
        docs_url="https://superset.apache.org/",
        env_keys=("SUPERSET_BASE_URL",),
        notes="External BI dashboards over the retail spine; SQLite now, optional Postgres later.",
    )

    def sync_inbound(self) -> IntegrationResult:
        dashboards = [
            {
                "id": "retail-inventory-health",
                "title": "Inventory Health",
                "source": "AI Retail OS spine",
                "url": f"{os.getenv('SUPERSET_BASE_URL', '').rstrip('/')}/superset/dashboard/retail-inventory-health"
                if os.getenv("SUPERSET_BASE_URL")
                else None,
            },
            {
                "id": "campaign-roi",
                "title": "Campaign ROI",
                "source": "AI Retail OS spine",
                "url": f"{os.getenv('SUPERSET_BASE_URL', '').rstrip('/')}/superset/dashboard/campaign-roi"
                if os.getenv("SUPERSET_BASE_URL")
                else None,
            },
        ]
        for dashboard in dashboards:
            store.cache_record("superset", "Dashboard", dashboard["id"], dashboard, dashboard["id"])
            store.record_external_ref("superset", "Dashboard", dashboard["id"], dashboard["id"], dashboard.get("url"))
        return IntegrationResult(
            status="success",
            records_read=len(dashboards),
            records_written=len(dashboards),
            summary={"mode": "mock" if not self.configured() else "connected_stub", "domains": {"Dashboard": len(dashboards)}},
        )


ADAPTERS: list[IntegrationAdapter] = [
    ERPNextAdapter(),
    MauticAdapter(),
    MedusaAdapter(),
    OpenBoxesAdapter(),
    AkeneoAdapter(),
    SupersetAdapter(),
]
