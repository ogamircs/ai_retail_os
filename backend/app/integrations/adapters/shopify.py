"""Shopify Plus adapter — Admin GraphQL sync, automatic-discount / fulfillment / campaign-brief drafts."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.integrations import store
from app.integrations.base import IntegrationAdapter, IntegrationDefinition, IntegrationResult
from app.integrations.http import (
    JsonHttpClient,
)
from app.spine.db import conn


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
            for idx, edge in enumerate(edges):
                node = edge.get("node")
                if isinstance(node, dict):
                    out.append(node)
                if max_rows is not None and len(out) >= max_rows:
                    page_info = conn_obj.get("pageInfo") or {}
                    # Truncated if we stopped mid-page (more edges left here)
                    # OR there's another page we won't fetch.
                    more_in_page = idx < len(edges) - 1
                    truncated = more_in_page or bool(page_info.get("hasNextPage"))
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
            loc_id = _shopify_gid_tail((node.get("location") or {}).get("id"))
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
        from datetime import timedelta

        pct = float(
            payload.get("discount_pct")
            or payload.get("discount_percent")
            or payload.get("percentage")
            or 0.20
        )
        if pct > 1:
            pct = pct / 100.0
        starts = datetime.now(UTC).replace(microsecond=0)
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

