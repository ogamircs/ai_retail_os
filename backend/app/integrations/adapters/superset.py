"""Apache Superset adapter — security login, dashboard / dataset sync (read-only by design)."""

from __future__ import annotations

import os
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote

from app.integrations import store
from app.integrations.base import IntegrationAdapter, IntegrationDefinition, IntegrationResult
from app.integrations.http import (
    JsonHttpClient,
    _coerce_id,
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

