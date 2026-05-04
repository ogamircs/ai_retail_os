"""ERPNext adapter — desk auth, item/warehouse/bin sync, PO + Pricing Rule drafts."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote, urlencode

from app.integrations import store
from app.integrations.base import IntegrationAdapter, IntegrationDefinition, IntegrationResult
from app.integrations.http import (
    JsonHttpClient,
    _display,
    _safe_float,
    _safe_int,
    _slug,
)
from app.spine.db import conn


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
        from datetime import datetime as _dt
        from datetime import timedelta

        annotated: list[str] = []
        bumped: list[dict[str, str]] = []
        unmatched: list[dict[str, str]] = []
        match_strategy: dict[str, str] = {}
        reason = payload.get("reason") or ""
        for po in pos:
            po_id = po.get("po_id")
            vendor = po.get("vendor")
            eta = (po.get("eta") or "").split("T")[0]

            # Preferred path: look up by external_ref so we hit the
            # exact ERPNext PO that mirrors our substrate row, immune
            # to vendor / schedule_date drift.
            po_name: str | None = None
            if po_id:
                ref = store.find_external_ref(
                    system_id=self.definition.system_id,
                    domain="Purchase Order",
                    local_id=str(po_id),
                )
                if ref:
                    po_name = ref["external_id"]
                    match_strategy[str(po_id)] = "external_ref"

            # Fallback: vendor + schedule_date filter. Brittle (any
            # operator-side reschedule on the desk side breaks it),
            # but covers POs created in ERPNext outside our sync.
            if not po_name:
                if not vendor or not eta:
                    unmatched.append({
                        "po_id": po_id,
                        "reason": (
                            "no external_ref and missing vendor/eta — re-sync "
                            "ERPNext or include both fields in the payload"
                        ),
                    })
                    continue
                filters = json.dumps([["supplier", "=", vendor], ["schedule_date", "=", eta]])
                body = self._client().request(
                    f"/api/resource/{quote('Purchase Order')}?filters={quote(filters)}&limit_page_length=1"
                )
                rows = body.get("data") or []
                if not rows:
                    unmatched.append({
                        "po_id": po_id,
                        "vendor": vendor,
                        "eta": eta,
                        "reason": (
                            "no PO matched supplier+schedule_date — vendor renamed "
                            "or schedule_date changed in ERPNext; re-sync to refresh "
                            "external_refs and retry"
                        ),
                    })
                    continue
                po_name = rows[0]["name"]
                if po_id:
                    match_strategy[str(po_id)] = "vendor+schedule_date"
            assert po_name is not None  # narrowed by both branches above
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
            "details": {
                "annotated": annotated,
                "bumped": bumped,
                "unmatched": unmatched,
                "match_strategy": match_strategy,
            },
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

