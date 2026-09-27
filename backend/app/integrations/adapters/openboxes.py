"""OpenBoxes adapter — session login, location / product / shipment sync, PO-hold annotations."""

from __future__ import annotations

import os
import re
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

