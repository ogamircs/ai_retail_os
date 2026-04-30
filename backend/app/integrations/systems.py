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
        for bin_row in bins:
            sku = bin_row.get("item_code")
            if sku:
                with conn() as c:
                    c.execute(
                        "UPDATE substrate_inventory SET on_hand = ? WHERE sku = ?",
                        (_safe_int(bin_row.get("actual_qty")), sku),
                    )
            self._cache("Bin", bin_row.get("name") or f"{sku}:{bin_row.get('warehouse')}", bin_row, sku)
            domains["Bin"] = domains.get("Bin", 0) + 1
            records_written += 1

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


class MauticAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="mautic",
        display_name="Mautic",
        domain="Marketing Automation",
        docs_url="https://devdocs.mautic.org/en/7.0/",
        env_keys=("MAUTIC_BASE_URL",),
        notes="Segments, email/campaign drafts, and webhook-based campaign telemetry.",
    )

    def healthcheck(self) -> IntegrationResult:
        if not self.configured():
            return super().healthcheck()
        try:  # pragma: no cover - live-system path
            headers = {}
            token = os.getenv("MAUTIC_BASIC_TOKEN")
            if token:
                headers["Authorization"] = f"Basic {token}"
            elif os.getenv("MAUTIC_USERNAME") and os.getenv("MAUTIC_PASSWORD"):
                raw = f"{os.environ['MAUTIC_USERNAME']}:{os.environ['MAUTIC_PASSWORD']}"
                headers["Authorization"] = f"Basic {b64encode(raw.encode()).decode()}"
            data = JsonHttpClient(os.environ["MAUTIC_BASE_URL"], headers=headers).request("/api/contacts?limit=1")
            return IntegrationResult(status="connected", summary={"contacts_seen": len(data.get("contacts", []))})
        except Exception as exc:
            return IntegrationResult(status="error", error=str(exc))

    def sync_inbound(self) -> IntegrationResult:
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
            summary={"mode": "mock" if not self.configured() else "connected_stub", "domains": domains},
        )

    def outbound_domain(self, action_type: str) -> str:
        return {
            "campaign_brief": "Segment Email",
            "campaign_launch": "Campaign",
            "campaign_measurement": "Campaign Report",
        }.get(action_type, super().outbound_domain(action_type))


class MedusaAdapter(IntegrationAdapter):
    definition = IntegrationDefinition(
        system_id="medusa",
        display_name="Medusa",
        domain="Ecommerce / OMS",
        docs_url="https://docs.medusajs.com/",
        env_keys=("MEDUSA_BASE_URL",),
        notes="Online orders, channels, inventory locations, reservations, and fulfillment routing.",
    )

    def sync_inbound(self) -> IntegrationResult:
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
            summary={"mode": "mock" if not self.configured() else "connected_stub", "domains": domains},
        )

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
