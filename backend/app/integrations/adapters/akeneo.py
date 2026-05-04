"""Akeneo PIM adapter — OAuth2 token, product / family / category sync, PIM enrich drafts."""

from __future__ import annotations

import json
import os
import re
from base64 import b64encode
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from app.integrations import store
from app.integrations.base import IntegrationAdapter, IntegrationDefinition, IntegrationResult
from app.integrations.http import (
    JsonHttpClient,
    _coerce_id,
)
from app.spine.db import conn

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

