"""Medusa adapter — admin login, sales-channel / location / product / order sync, fulfillment + transfer drafts."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode

from app.integrations import store
from app.integrations.base import IntegrationAdapter, IntegrationDefinition, IntegrationResult
from app.integrations.http import (
    JsonHttpClient,
    _coerce_id,
)
from app.spine.db import conn


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
