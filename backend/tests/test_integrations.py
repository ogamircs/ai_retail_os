import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.spine import db
from app.integrations import registry
from app.substrate import omnichannel, seed


INTEGRATION_ENV_KEYS = [
    "ERPNEXT_BASE_URL",
    "ERPNEXT_API_KEY",
    "ERPNEXT_API_SECRET",
    "MAUTIC_BASE_URL",
    "MAUTIC_USERNAME",
    "MAUTIC_PASSWORD",
    "MAUTIC_BASIC_TOKEN",
    "MEDUSA_BASE_URL",
    "MEDUSA_API_KEY",
    "OPENBOXES_BASE_URL",
    "OPENBOXES_USERNAME",
    "OPENBOXES_PASSWORD",
    "AKENEO_BASE_URL",
    "AKENEO_CLIENT_ID",
    "AKENEO_SECRET",
    "AKENEO_USERNAME",
    "AKENEO_PASSWORD",
    "SUPERSET_BASE_URL",
    "SHOPIFY_SHOP_DOMAIN",
    "SHOPIFY_ADMIN_TOKEN",
    "SHOPIFY_API_VERSION",
    "KLAVIYO_API_KEY",
]


class IntegrationLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        self.old_env = {key: os.environ.get(key) for key in INTEGRATION_ENV_KEYS}
        for key in INTEGRATION_ENV_KEYS:
            os.environ.pop(key, None)
        seed.seed()

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_system_registry_defaults_to_mock_mode(self):
        systems = registry.list_systems()
        self.assertEqual(len(systems), 7)
        ids = {system["system_id"] for system in systems}
        self.assertIn("erpnext", ids)
        self.assertIn("shopify", ids)
        self.assertTrue(all(system["mode"] == "mock" for system in systems))
        self.assertTrue(all(system["configured"] is False for system in systems))

    def test_mock_erpnext_sync_creates_cache_and_external_refs(self):
        result = registry.sync_system("erpnext")
        self.assertEqual(result["result"]["status"], "success")
        self.assertGreater(result["result"]["records_written"], 0)
        records = registry.list_records(system_id="erpnext", domain="Item")
        self.assertGreater(len(records["records"]), 0)
        self.assertGreater(len(records["external_refs"]), 0)
        systems = {system["system_id"]: system for system in registry.list_systems()}
        self.assertIsNotNone(systems["erpnext"]["last_sync_ts"])

    def test_action_tools_create_approval_gated_outbox_actions(self):
        promotion = omnichannel.create_promotion(
            category="summer_apparel",
            offer="25% member summer edit",
            discount_percent=25,
            reason="test",
        )
        self.assertIn("external_actions", promotion)
        self.assertEqual(promotion["external_actions"][0]["system_id"], "erpnext")
        self.assertEqual(promotion["external_actions"][0]["status"], "mock_only")
        actions = omnichannel.list_action_queue()
        self.assertGreaterEqual(len(actions[0]["external_actions"]), 1)

    def test_apply_outbound_action_is_manual_and_mock_safe(self):
        promotion = omnichannel.create_promotion(
            category="summer_apparel",
            offer="25% member summer edit",
            discount_percent=25,
            reason="test",
        )
        outbox = promotion["external_actions"][0]
        with TestClient(app) as client:
            response = client.post(f"/api/integrations/erpnext/actions/{outbox['id']}/apply")
        self.assertEqual(response.status_code, 200)
        applied = response.json()
        self.assertEqual(applied["status"], "applied_mock")
        self.assertIn("no external system was mutated", applied["result"]["message"])

    def test_mautic_list_paginates_until_total_consumed(self):
        """Pin the pagination guard: a single Mautic instance with more rows
        than `limit` would otherwise be truncated to the first page.
        Walk `start` until `total` is consumed.
        """
        from app.integrations.systems import MauticAdapter

        adapter = MauticAdapter()
        # Build 7 fake rows; ask for limit=3. Should fetch 3 pages: 3+3+1.
        all_rows = [{"id": str(i)} for i in range(1, 8)]
        calls: list[str] = []

        class _StubClient:
            def request(self, path: str):
                calls.append(path)
                # Parse start + limit from the querystring (cheap).
                qs = path.split("?", 1)[1]
                params = dict(p.split("=", 1) for p in qs.split("&"))
                start = int(params["start"])
                limit = int(params["limit"])
                page = all_rows[start : start + limit]
                rows = {row["id"]: row for row in page}
                return {"lists": rows, "total": len(all_rows)}

        adapter._client = lambda: _StubClient()  # type: ignore[method-assign]
        out, truncated = adapter._mautic_list("segments", "lists", limit=3)
        self.assertEqual(len(out), 7)
        self.assertFalse(truncated)
        # Three pages: start=0,3,6.
        self.assertEqual(len(calls), 3)
        # Confirm we fetched every row, not just the first page.
        self.assertEqual({r["id"] for r in out}, {str(i) for i in range(1, 8)})

    def test_mautic_list_stops_on_short_page_when_total_missing(self):
        """Some Mautic responses omit `total`. Fall back to the short-page
        heuristic — a page shorter than the requested limit means we're done.
        """
        from app.integrations.systems import MauticAdapter

        adapter = MauticAdapter()
        all_rows = [{"id": str(i)} for i in range(1, 5)]  # 4 rows

        class _NoTotalClient:
            def request(self, path: str):
                qs = path.split("?", 1)[1]
                params = dict(p.split("=", 1) for p in qs.split("&"))
                start = int(params["start"])
                limit = int(params["limit"])
                page = all_rows[start : start + limit]
                return {"lists": {r["id"]: r for r in page}}  # no `total`

        adapter._client = lambda: _NoTotalClient()  # type: ignore[method-assign]
        out, truncated = adapter._mautic_list("segments", "lists", limit=3)
        # Two pages: 3 (full) + 1 (short → stop).
        self.assertEqual(len(out), 4)
        self.assertFalse(truncated)

    def test_mautic_apply_campaign_launch_creates_draft(self):
        """`campaign_launch` apply path POSTs /api/campaigns/new and writes
        the returned id back to outbox_actions as draft_created.
        """
        from app.integrations.systems import MauticAdapter
        from app.integrations import store

        os.environ["MAUTIC_BASE_URL"] = "http://stub"
        os.environ["MAUTIC_USERNAME"] = "admin"
        os.environ["MAUTIC_PASSWORD"] = "pw"

        adapter = MauticAdapter()
        self.assertTrue(adapter.configured())

        row = store.create_outbox_action(
            system_id="mautic",
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_launch",
            title="Summer Apparel Push",
            external_domain="Campaign",
            payload={
                "campaign_id": "cmp-test1",
                "category": "summer_apparel",
                "segment_id": "seg-vacation",
                "channel": "paid social",
                "offer": "25% off",
                "budget": 12000,
                "projected_lift": 0.2,
                "projected_roi": 1.8,
            },
            configured=True,
        )
        action_id = row["id"]

        captured: dict = {}

        class _StubClient:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                captured["path"] = path
                captured["method"] = method
                captured["payload"] = payload
                return {"campaign": {"id": 4242, "name": "Summer Apparel Push"}}

        adapter._client = lambda: _StubClient()  # type: ignore[method-assign]
        result = adapter.apply_outbound(action_id)
        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(str(result["external_id"]), "4242")
        self.assertEqual(captured["method"], "POST")
        self.assertIn("/api/campaigns/new", captured["path"])
        self.assertEqual(captured["payload"]["isPublished"], False)
        self.assertIn("[retail-os:cmp-test1]", captured["payload"]["description"])

    def test_mautic_apply_campaign_brief_reuses_existing_segment(self):
        """`campaign_brief` apply path is idempotent — if a segment with the
        derived alias exists, return its id rather than POSTing /new.
        """
        from app.integrations.systems import MauticAdapter
        from app.integrations import store

        os.environ["MAUTIC_BASE_URL"] = "http://stub"
        os.environ["MAUTIC_USERNAME"] = "admin"
        os.environ["MAUTIC_PASSWORD"] = "pw"

        adapter = MauticAdapter()
        row = store.create_outbox_action(
            system_id="mautic",
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_brief",
            title="Summer Apparel Brief",
            external_domain="Segment Email",
            payload={
                "segment_id": "seg-vacation",
                "segment_name": "Vacation planners",
                "category": "summer_apparel",
                "channel": "paid social",
                "offer": "25% off",
            },
            configured=True,
        )
        action_id = row["id"]

        calls: list[tuple[str, str]] = []

        class _ExistingClient:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                calls.append((method, path))
                # Return an existing list on the alias-eq lookup.
                if "where" in path and "alias" in path:
                    return {"lists": {"7": {"id": 7, "alias": "seg_vacation"}}}
                # If we ever POST, the test will catch it via the assertion.
                return {}

        adapter._client = lambda: _ExistingClient()  # type: ignore[method-assign]
        result = adapter.apply_outbound(action_id)
        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(str(result["external_id"]), "7")
        # No POST should have been issued — segment already existed.
        self.assertTrue(all(method == "GET" for method, _ in calls))

    def test_mautic_apply_unsupported_action_falls_back_to_mock(self):
        """Action types not in LIVE_ACTION_TYPES should fall through to the
        base mock-apply behaviour even when the adapter is configured.
        """
        from app.integrations.systems import MauticAdapter
        from app.integrations import store

        os.environ["MAUTIC_BASE_URL"] = "http://stub"
        os.environ["MAUTIC_USERNAME"] = "admin"
        os.environ["MAUTIC_PASSWORD"] = "pw"

        adapter = MauticAdapter()
        row = store.create_outbox_action(
            system_id="mautic",
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_measurement",
            title="Measure cmp-x",
            external_domain="Campaign Report",
            payload={"campaign_id": "cmp-x"},
            configured=True,
        )
        action_id = row["id"]
        # No _client stub; if dispatch tried to talk to the network, this
        # would raise. The fallback should never reach _dispatch_outbound.
        # Base adapter returns draft_created for configured / applied_mock
        # for unconfigured — in both cases the unsupported type was a no-op.
        result = adapter.apply_outbound(action_id)
        self.assertEqual(result["status"], "draft_created")

    def test_mautic_apply_missing_payload_lands_in_error(self):
        """campaign_launch with no campaign_id has nothing to write — the
        outcome carries no external_id, so the row must land in `error`,
        not `draft_created`.
        """
        from app.integrations.systems import MauticAdapter
        from app.integrations import store

        os.environ["MAUTIC_BASE_URL"] = "http://stub"
        os.environ["MAUTIC_USERNAME"] = "admin"
        os.environ["MAUTIC_PASSWORD"] = "pw"

        adapter = MauticAdapter()
        row = store.create_outbox_action(
            system_id="mautic",
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_launch",
            title="Bad payload",
            external_domain="Campaign",
            payload={},  # no campaign_id
            configured=True,
        )
        action_id = row["id"]
        # No client stub — the helper should bail before calling _client().
        result = adapter.apply_outbound(action_id)
        self.assertEqual(result["status"], "error")
        self.assertIn("campaign_id", result["result"]["error"])

    def test_medusa_apply_store_transfer_records_metadata(self):
        """`store_transfer` apply → appends to the from-store stock_location's
        `metadata.retail_os_pending_transfers` and writes draft_created.
        """
        from app.integrations.systems import MedusaAdapter
        from app.integrations import store

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"

        adapter = MedusaAdapter()
        adapter._admin_token = "stub"
        adapter._login = lambda: "stub"  # type: ignore[method-assign]

        row = store.create_outbox_action(
            system_id="medusa",
            action_queue_id=None,
            agent="Merchandiser",
            action_type="store_transfer",
            title="Rebalance summer apparel",
            external_domain="Reservation",
            payload={
                "category": "summer_apparel",
                "from_store": "sto-chi",
                "to_store": "sto-mia",
                "qty": 24,
                "reason": "demand spike",
            },
            configured=True,
        )
        action_id = row["id"]

        captured: dict = {"posts": []}

        class _Stub:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                if path.startswith("/admin/stock-locations") and method == "GET":
                    return {
                        "stock_locations": [
                            {"id": "loc_chi", "name": "Chicago", "metadata": {"retail_os_store_id": "sto-chi"}},
                            {"id": "loc_mia", "name": "Miami", "metadata": {"retail_os_store_id": "sto-mia"}},
                        ],
                        "count": 2,
                    }
                if path.startswith("/admin/stock-locations/loc_chi") and method == "POST":
                    captured["posts"].append({"path": path, "payload": payload})
                    return {"stock_location": {"id": "loc_chi", "metadata": payload.get("metadata", {})}}
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter.apply_outbound(action_id)
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertEqual(result["external_id"], "loc_chi")
        self.assertEqual(len(captured["posts"]), 1)
        log = captured["posts"][0]["payload"]["metadata"]["retail_os_pending_transfers"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["category"], "summer_apparel")
        self.assertEqual(log[0]["to_store"], "sto-mia")

    def test_medusa_apply_store_transfer_is_idempotent(self):
        """Re-applying the same payload (same marker) should not duplicate
        the log entry. The second call returns reused=True.
        """
        from app.integrations.systems import MedusaAdapter
        from app.integrations import store

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"

        adapter = MedusaAdapter()
        adapter._admin_token = "stub"
        adapter._login = lambda: "stub"  # type: ignore[method-assign]

        payload = {
            "category": "tech_accessories",
            "from_store": "sto-nyc",
            "to_store": "sto-sea",
            "qty": 12,
            "reason": "test",
        }
        marker = MedusaAdapter._payload_marker(payload)
        existing_log = [{"marker": marker, "to_store": "sto-sea"}]

        row = store.create_outbox_action(
            system_id="medusa",
            action_queue_id=None,
            agent="Merchandiser",
            action_type="store_transfer",
            title="Re-apply same transfer",
            external_domain="Reservation",
            payload=payload,
            configured=True,
        )

        posts: list[dict] = []

        class _Stub:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                if path.startswith("/admin/stock-locations") and method == "GET":
                    return {
                        "stock_locations": [
                            {
                                "id": "loc_nyc",
                                "metadata": {
                                    "retail_os_store_id": "sto-nyc",
                                    "retail_os_pending_transfers": existing_log,
                                },
                            }
                        ],
                        "count": 1,
                    }
                posts.append({"path": path, "payload": payload})
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(result["external_id"], "loc_nyc")
        details = result.get("result", {}).get("details") or {}
        self.assertTrue(details.get("reused"))
        # No POST should fire — log already had the marker.
        self.assertEqual(posts, [])

    def test_medusa_apply_fulfillment_routing_records_on_sales_channel(self):
        from app.integrations.systems import MedusaAdapter
        from app.integrations import store

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"

        adapter = MedusaAdapter()
        adapter._admin_token = "stub"
        adapter._login = lambda: "stub"  # type: ignore[method-assign]

        row = store.create_outbox_action(
            system_id="medusa",
            action_queue_id=None,
            agent="Fulfillment",
            action_type="fulfillment_routing",
            title="Route summer demand",
            external_domain="Fulfillment",
            payload={
                "category": "summer_apparel",
                "recommended_strategy": "favor BOPIS",
                "guardrail": "skip high-labor stores",
            },
            configured=True,
        )

        posts: list[dict] = []

        class _Stub:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                if path.startswith("/admin/sales-channels") and method == "GET":
                    return {
                        "sales_channels": [
                            {"id": "sc_demo", "name": "Retail Demo", "metadata": {}},
                            {"id": "sc_other", "name": "Other", "metadata": {}},
                        ],
                        "count": 2,
                    }
                if path.startswith("/admin/sales-channels/sc_demo") and method == "POST":
                    posts.append({"path": path, "payload": payload})
                    return {}
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertEqual(result["external_id"], "sc_demo")
        self.assertEqual(len(posts), 1)
        log = posts[0]["payload"]["metadata"]["retail_os_routing_log"]
        self.assertEqual(log[0]["strategy"], "favor BOPIS")

    def test_medusa_apply_missing_from_store_lands_in_error(self):
        from app.integrations.systems import MedusaAdapter
        from app.integrations import store

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"

        adapter = MedusaAdapter()
        adapter._admin_token = "stub"
        adapter._login = lambda: "stub"  # type: ignore[method-assign]
        # No client stub: dispatch must bail before any HTTP call.

        row = store.create_outbox_action(
            system_id="medusa",
            action_queue_id=None,
            agent="Merchandiser",
            action_type="store_transfer",
            title="Bad payload",
            external_domain="Reservation",
            payload={"category": "summer_apparel"},  # no from_store
            configured=True,
        )
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "error")
        self.assertIn("from_store", result["result"]["error"])

    def test_medusa_apply_unsupported_action_falls_back_to_mock(self):
        """Action types not in LIVE_ACTION_TYPES fall through to base mock-apply."""
        from app.integrations.systems import MedusaAdapter
        from app.integrations import store

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"

        adapter = MedusaAdapter()
        row = store.create_outbox_action(
            system_id="medusa",
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_brief",
            title="Bogus type",
            external_domain="Campaign Report",
            payload={"category": "x"},
            configured=True,
        )
        # No _client stub — base must never reach _dispatch_outbound.
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created")

    def test_medusa_admin_list_paginates_until_count_consumed(self):
        """`MedusaAdapter._admin_list` walks offset until `count` is met.
        A single Medusa instance with more rows than `limit` would otherwise
        be truncated at page 1.
        """
        from app.integrations.systems import MedusaAdapter

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"

        adapter = MedusaAdapter()
        all_rows = [{"id": f"prd_{i}"} for i in range(1, 8)]  # 7 rows

        class _StubClient:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                qs = path.split("?", 1)[1]
                params = dict(p.split("=", 1) for p in qs.split("&"))
                offset = int(params["offset"])
                limit = int(params["limit"])
                page = all_rows[offset : offset + limit]
                return {"products": page, "count": len(all_rows)}

        adapter._admin_token = "stub-token"
        adapter._client = lambda: _StubClient()  # type: ignore[method-assign]
        out, truncated = adapter._admin_list("products", "products", limit=3)
        self.assertEqual(len(out), 7)
        self.assertFalse(truncated)
        self.assertEqual({r["id"] for r in out}, {f"prd_{i}" for i in range(1, 8)})

    def test_medusa_admin_list_flags_truncation_when_max_rows_hit(self):
        from app.integrations.systems import MedusaAdapter

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"

        adapter = MedusaAdapter()
        all_rows = [{"id": str(i)} for i in range(1, 11)]

        class _BigClient:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                qs = path.split("?", 1)[1]
                params = dict(p.split("=", 1) for p in qs.split("&"))
                offset = int(params["offset"])
                limit = int(params["limit"])
                page = all_rows[offset : offset + limit]
                return {"products": page, "count": len(all_rows)}

        adapter._admin_token = "stub-token"
        adapter._client = lambda: _BigClient()  # type: ignore[method-assign]
        out, truncated = adapter._admin_list("products", "products", limit=3, max_rows=5)
        self.assertEqual(len(out), 5)
        self.assertTrue(truncated)

    def test_medusa_admin_request_relogins_on_401(self):
        """A stale JWT (expired or revoked admin-side) would otherwise
        get the adapter stuck returning 401s until restart. _admin_request
        drops the cached token on 401, re-logins, retries once.
        """
        from urllib.error import HTTPError
        from app.integrations.systems import MedusaAdapter

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"

        adapter = MedusaAdapter()
        adapter._admin_token = "stale-token"

        login_calls: list[int] = []
        request_calls: list[str] = []

        def _fake_login() -> str:
            login_calls.append(1)
            adapter._admin_token = f"fresh-{len(login_calls)}"
            return adapter._admin_token

        adapter._login = _fake_login  # type: ignore[method-assign]

        class _FlakyClient:
            def __init__(self, token: str):
                self.token = token

            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                request_calls.append(self.token)
                if self.token == "stale-token":
                    raise HTTPError(
                        url=path, code=401, msg="Unauthorized", hdrs=None, fp=None
                    )
                return {"products": [{"id": "prd_1"}], "count": 1}

        # Mirror the real _client behaviour: re-login first, then build
        # the http client with whatever token is current. Without this the
        # second call after `_admin_token = None` wouldn't pick up the
        # refreshed token.
        def _stubbed_client():
            if not adapter._admin_token:
                adapter._login()
            return _FlakyClient(adapter._admin_token)

        adapter._client = _stubbed_client  # type: ignore[method-assign]

        out = adapter._admin_request("/admin/products?limit=1")
        self.assertEqual(out, {"products": [{"id": "prd_1"}], "count": 1})
        # First call uses stale token (401), then re-login fires once,
        # second call uses the fresh token and succeeds.
        self.assertEqual(request_calls, ["stale-token", "fresh-1"])
        self.assertEqual(len(login_calls), 1)

    def test_medusa_admin_request_does_not_retry_on_non_401(self):
        """A 500 (or any non-401) should propagate — re-login won't help
        and would mask real upstream errors.
        """
        from urllib.error import HTTPError
        from app.integrations.systems import MedusaAdapter

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"

        adapter = MedusaAdapter()
        adapter._admin_token = "some-token"
        login_calls: list[int] = []
        adapter._login = lambda: (login_calls.append(1) or "tok")  # type: ignore[method-assign]

        class _ServerErrClient:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                raise HTTPError(url=path, code=500, msg="boom", hdrs=None, fp=None)

        adapter._client = lambda: _ServerErrClient()  # type: ignore[method-assign]
        with self.assertRaises(HTTPError):
            adapter._admin_request("/admin/products?limit=1")
        self.assertEqual(len(login_calls), 0)  # no retry, no relogin

    def test_medusa_configured_requires_admin_creds(self):
        """env_keys only checks MEDUSA_BASE_URL; the override must also
        require admin email + password — without them there's nothing to
        log in with.
        """
        from app.integrations.systems import MedusaAdapter

        os.environ["MEDUSA_BASE_URL"] = "http://stub"
        os.environ.pop("MEDUSA_ADMIN_EMAIL", None)
        os.environ.pop("MEDUSA_ADMIN_PASSWORD", None)
        self.assertFalse(MedusaAdapter().configured())

        os.environ["MEDUSA_ADMIN_EMAIL"] = "admin@retail.local"
        self.assertFalse(MedusaAdapter().configured())  # still missing pw

        os.environ["MEDUSA_ADMIN_PASSWORD"] = "pw"
        self.assertTrue(MedusaAdapter().configured())

    def test_openboxes_configured_requires_auth(self):
        """env_keys only checks OPENBOXES_BASE_URL; the override must
        also require a token OR user/password.
        """
        from app.integrations.systems import OpenBoxesAdapter

        os.environ["OPENBOXES_BASE_URL"] = "http://stub"
        os.environ.pop("OPENBOXES_USERNAME", None)
        os.environ.pop("OPENBOXES_PASSWORD", None)
        os.environ.pop("OPENBOXES_API_TOKEN", None)
        self.assertFalse(OpenBoxesAdapter().configured())

        os.environ["OPENBOXES_API_TOKEN"] = "tok"
        self.assertTrue(OpenBoxesAdapter().configured())
        os.environ.pop("OPENBOXES_API_TOKEN")

        os.environ["OPENBOXES_USERNAME"] = "openboxes"
        self.assertFalse(OpenBoxesAdapter().configured())  # still missing pw
        os.environ["OPENBOXES_PASSWORD"] = "pw"
        self.assertTrue(OpenBoxesAdapter().configured())

    def test_openboxes_admin_request_relogins_on_401(self):
        """Mirror of MedusaAdapter.test_admin_request_relogins_on_401:
        stale token → drop, re-login, retry once.
        """
        from urllib.error import HTTPError
        from app.integrations.systems import OpenBoxesAdapter

        os.environ["OPENBOXES_BASE_URL"] = "http://stub"
        os.environ["OPENBOXES_USERNAME"] = "openboxes"
        os.environ["OPENBOXES_PASSWORD"] = "pw"
        os.environ.pop("OPENBOXES_API_TOKEN", None)

        adapter = OpenBoxesAdapter()
        adapter._auth_token = "stale"

        login_calls: list[int] = []

        def _fake_login() -> str:
            login_calls.append(1)
            adapter._auth_token = f"fresh-{len(login_calls)}"
            return adapter._auth_token

        adapter._login = _fake_login  # type: ignore[method-assign]

        request_calls: list[str] = []

        class _Flaky:
            def __init__(self, token: str):
                self.token = token

            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                request_calls.append(self.token)
                if self.token == "stale":
                    raise HTTPError(url=path, code=401, msg="Unauthorized", hdrs=None, fp=None)
                return {"data": [{"id": "loc_1"}]}

        def _stubbed_client():
            if not adapter._auth_token:
                adapter._login()
            return _Flaky(adapter._auth_token)

        adapter._client = _stubbed_client  # type: ignore[method-assign]

        out = adapter._admin_request("/api/locations?max=1")
        self.assertEqual(out, {"data": [{"id": "loc_1"}]})
        self.assertEqual(request_calls, ["stale", "fresh-1"])
        self.assertEqual(len(login_calls), 1)

    def test_openboxes_apply_po_held_annotates_matching_shipment(self):
        """`po_held` apply: payload.pos[*].po_id matches shipments by
        name; helper POSTs a comment per match and returns the first
        shipment id.
        """
        from app.integrations.systems import OpenBoxesAdapter
        from app.integrations import store

        os.environ["OPENBOXES_BASE_URL"] = "http://stub"
        os.environ["OPENBOXES_API_TOKEN"] = "tok"

        adapter = OpenBoxesAdapter()
        adapter._auth_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        row = store.create_outbox_action(
            system_id="openboxes",
            action_queue_id=None,
            agent="Replenishment",
            action_type="po_held",
            title="Hold inbound POs",
            external_domain="Purchase Order",
            payload={
                "pos": [{"po_id": "PO-101"}, {"po_id": "PO-999"}],
                "reason": "weather risk",
            },
            configured=True,
        )

        posts: list[dict] = []

        class _Stub:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                if path.startswith("/api/shipments?direction=INBOUND") and method == "GET":
                    return {"data": [{"id": "ship_1", "name": "PO-101"}]}
                if path.startswith("/api/shipments/ship_1/comments") and method == "POST":
                    posts.append({"path": path, "payload": payload})
                    return {"data": {"id": "cmt_1"}}
                if path.startswith("/api/shipments") and method == "GET":
                    return {"data": [{"id": "ship_1", "name": "PO-101"}]}
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]

        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertEqual(result["external_id"], "ship_1")
        self.assertEqual(len(posts), 1)
        self.assertIn("po_held", posts[0]["payload"]["comment"])

    def test_openboxes_apply_no_matching_shipment_lands_in_error(self):
        """payload.pos[*].po_id with no matching shipment → no external_id
        → row lands in `error` (not `draft_created`).
        """
        from app.integrations.systems import OpenBoxesAdapter
        from app.integrations import store

        os.environ["OPENBOXES_BASE_URL"] = "http://stub"
        os.environ["OPENBOXES_API_TOKEN"] = "tok"

        adapter = OpenBoxesAdapter()
        adapter._auth_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        row = store.create_outbox_action(
            system_id="openboxes",
            action_queue_id=None,
            agent="Replenishment",
            action_type="po_held",
            title="Hold inbound POs",
            external_domain="Purchase Order",
            payload={"pos": [{"po_id": "PO-DOES-NOT-EXIST"}], "reason": "x"},
            configured=True,
        )

        class _Empty:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                if path.startswith("/api/shipments"):
                    return {"data": []}
                return {}

        adapter._client = lambda: _Empty()  # type: ignore[method-assign]
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "error")
        self.assertIn("no matching inbound shipments", result["result"]["error"].lower() if result["result"]["error"] else "")

    def test_openboxes_apply_indexes_by_shipment_number(self):
        """When a shipment has both `name` and `shipmentNumber` populated
        and the payload `po_id` matches the `shipmentNumber`, the helper
        must still resolve. Indexing on `name OR shipmentNumber` would
        silently miss this case.
        """
        from app.integrations.systems import OpenBoxesAdapter
        from app.integrations import store

        os.environ["OPENBOXES_BASE_URL"] = "http://stub"
        os.environ["OPENBOXES_API_TOKEN"] = "tok"

        adapter = OpenBoxesAdapter()
        adapter._auth_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        row = store.create_outbox_action(
            system_id="openboxes",
            action_queue_id=None,
            agent="Replenishment",
            action_type="po_held",
            title="Hold by shipmentNumber",
            external_domain="Purchase Order",
            payload={"pos": [{"po_id": "PO-555"}], "reason": "weather"},
            configured=True,
        )

        posts: list[dict] = []

        class _Stub:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                if path.startswith("/api/shipments?direction=INBOUND") and method == "GET":
                    return {
                        "data": [
                            {
                                "id": "ship_x",
                                "name": "Internal-Label-Foo",
                                "shipmentNumber": "PO-555",
                            }
                        ]
                    }
                if path.startswith("/api/shipments/ship_x/comments") and method == "POST":
                    posts.append({"path": path, "payload": payload})
                    return {}
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertEqual(result["external_id"], "ship_x")
        self.assertEqual(len(posts), 1)

    def test_openboxes_apply_does_not_fall_back_on_empty_inbound(self):
        """Empty inbound result is a legitimate "no inbound shipments"
        signal, not a request failure. Falling back to the unfiltered
        shipments list could annotate an outbound record on a name/
        shipmentNumber collision. Helper must keep the empty list
        and land the row in `error`.
        """
        from app.integrations.systems import OpenBoxesAdapter
        from app.integrations import store

        os.environ["OPENBOXES_BASE_URL"] = "http://stub"
        os.environ["OPENBOXES_API_TOKEN"] = "tok"

        adapter = OpenBoxesAdapter()
        adapter._auth_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        row = store.create_outbox_action(
            system_id="openboxes",
            action_queue_id=None,
            agent="Replenishment",
            action_type="po_held",
            title="Hold colliding po",
            external_domain="Purchase Order",
            payload={"pos": [{"po_id": "PO-101"}], "reason": "x"},
            configured=True,
        )

        calls: list[tuple[str, str]] = []

        class _Stub:
            def request(self, path: str, method: str = "GET", payload: dict | None = None):
                calls.append((method, path))
                if "direction=INBOUND" in path:
                    return {"data": []}
                # Outbound shipment that happens to share the po_id name.
                # If the helper falls back to this list, we'd annotate it.
                if path.startswith("/api/shipments") and method == "GET":
                    return {"data": [{"id": "ship_outbound", "name": "PO-101"}]}
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "error", msg=result)
        # Only the inbound-filtered call should have fired; no fallback
        # to the unfiltered list, no comment POST.
        get_paths = [p for m, p in calls if m == "GET"]
        self.assertEqual(len(get_paths), 1)
        self.assertIn("direction=INBOUND", get_paths[0])
        self.assertFalse(any(m == "POST" for m, _ in calls))

    def test_akeneo_configured_requires_oauth_credentials(self):
        from app.integrations.systems import AkeneoAdapter

        os.environ["AKENEO_BASE_URL"] = "http://stub"
        for k in ("AKENEO_CLIENT_ID", "AKENEO_SECRET", "AKENEO_USERNAME", "AKENEO_PASSWORD"):
            os.environ.pop(k, None)
        self.assertFalse(AkeneoAdapter().configured())

        os.environ["AKENEO_CLIENT_ID"] = "cid"
        os.environ["AKENEO_SECRET"] = "sec"
        self.assertFalse(AkeneoAdapter().configured())  # missing user/pw

        os.environ["AKENEO_USERNAME"] = "admin"
        os.environ["AKENEO_PASSWORD"] = "pw"
        self.assertTrue(AkeneoAdapter().configured())

    def test_akeneo_api_list_walks_paginated_links(self):
        """Akeneo paginates via _links.next; helper must follow until exhausted."""
        from app.integrations.systems import AkeneoAdapter

        os.environ["AKENEO_BASE_URL"] = "http://stub"
        os.environ["AKENEO_CLIENT_ID"] = "cid"
        os.environ["AKENEO_SECRET"] = "sec"
        os.environ["AKENEO_USERNAME"] = "admin"
        os.environ["AKENEO_PASSWORD"] = "pw"

        adapter = AkeneoAdapter()
        adapter._admin_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        pages = {
            "/api/rest/v1/products?limit=100": {
                "_embedded": {"items": [{"identifier": "A"}, {"identifier": "B"}]},
                "_links": {"next": {"href": "http://stub/api/rest/v1/products?page=2"}},
            },
            "/api/rest/v1/products?page=2": {
                "_embedded": {"items": [{"identifier": "C"}]},
                # no _links.next → stop
            },
        }

        class _Stub:
            def request(self, path, method="GET", payload=None):
                return pages.get(path, {})

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        out = adapter._api_list("products")
        self.assertEqual([r["identifier"] for r in out], ["A", "B", "C"])

    def test_akeneo_api_list_handles_proxy_rewritten_next_links(self):
        """Akeneo's _links.next.href can carry a different scheme/host
        than AKENEO_BASE_URL (proxy / canonical rewrite). Pagination
        must still work — strip to path+query rather than relying on
        startswith(base_url).
        """
        from app.integrations.systems import AkeneoAdapter

        os.environ["AKENEO_BASE_URL"] = "http://stub"
        os.environ["AKENEO_CLIENT_ID"] = "cid"
        os.environ["AKENEO_SECRET"] = "sec"
        os.environ["AKENEO_USERNAME"] = "admin"
        os.environ["AKENEO_PASSWORD"] = "pw"

        adapter = AkeneoAdapter()
        adapter._admin_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        seen: list[str] = []
        pages = {
            "/api/rest/v1/products?limit=100": {
                "_embedded": {"items": [{"identifier": "A"}]},
                # Different host (proxy rewrite). Old code would forward
                # this absolute URL and JsonHttpClient would prepend
                # AKENEO_BASE_URL, producing junk.
                "_links": {"next": {"href": "https://akeneo.internal.example/api/rest/v1/products?page=2"}},
            },
            "/api/rest/v1/products?page=2": {
                "_embedded": {"items": [{"identifier": "B"}]},
            },
        }

        class _Stub:
            def request(self, path, method="GET", payload=None):
                seen.append(path)
                return pages.get(path, {})

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        out = adapter._api_list("products")
        self.assertEqual([r["identifier"] for r in out], ["A", "B"])
        # Both calls must have hit relative paths the JsonHttpClient
        # can prepend its base to.
        self.assertEqual(
            seen,
            ["/api/rest/v1/products?limit=100", "/api/rest/v1/products?page=2"],
        )

    def test_akeneo_live_sync_pulls_uuid_endpoint_for_identifier_less_products(self):
        """Akeneo CE 7+ identifier-based /api/rest/v1/products doesn't
        return products without an identifier — those only show up under
        /api/rest/v1/products-uuid. Sync must query both and dedupe so
        UUID-only products land in record_cache.
        """
        from app.integrations.systems import AkeneoAdapter
        from app.integrations import store

        os.environ["AKENEO_BASE_URL"] = "http://stub"
        os.environ["AKENEO_CLIENT_ID"] = "cid"
        os.environ["AKENEO_SECRET"] = "sec"
        os.environ["AKENEO_USERNAME"] = "admin"
        os.environ["AKENEO_PASSWORD"] = "pw"

        adapter = AkeneoAdapter()
        adapter._admin_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        # uuid endpoint returns ALL products (with + without identifier)
        # legacy /products endpoint returns only ones with identifiers.
        # Dedup must collapse the overlap.
        uuid_endpoint = [
            {"identifier": "SKU-A", "uuid": "uuid-a"},
            {"identifier": None, "uuid": "uuid-b"},
        ]
        legacy_endpoint = [
            {"identifier": "SKU-A", "uuid": "uuid-a"},  # duplicate of uuid endpoint
        ]

        class _Stub:
            def request(self, path, method="GET", payload=None):
                if path.startswith("/api/rest/v1/categories"):
                    return {"_embedded": {"items": []}}
                if path.startswith("/api/rest/v1/products-uuid"):
                    return {"_embedded": {"items": uuid_endpoint}}
                if path.startswith("/api/rest/v1/products"):
                    return {"_embedded": {"items": legacy_endpoint}}
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter.sync_inbound()
        self.assertEqual(result.status, "success")
        # Two unique products (SKU-A from either endpoint, uuid-b from
        # uuid endpoint only). The duplicate must NOT inflate the count.
        self.assertEqual(result.summary["domains"].get("Product"), 2)
        bundle = store.list_records(system_id="akeneo", domain="Product", limit=10)
        self.assertEqual(
            {r.get("external_id") for r in bundle["records"]},
            {"SKU-A", "uuid-b"},
        )

    def test_akeneo_live_sync_tolerates_missing_uuid_endpoint(self):
        """Older Akeneo minors don't expose /products-uuid (404). The
        sync must keep going against /products instead of failing the
        whole run.
        """
        from urllib.error import HTTPError
        from app.integrations.systems import AkeneoAdapter

        os.environ["AKENEO_BASE_URL"] = "http://stub"
        os.environ["AKENEO_CLIENT_ID"] = "cid"
        os.environ["AKENEO_SECRET"] = "sec"
        os.environ["AKENEO_USERNAME"] = "admin"
        os.environ["AKENEO_PASSWORD"] = "pw"

        adapter = AkeneoAdapter()
        adapter._admin_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        class _Stub:
            def request(self, path, method="GET", payload=None):
                if path.startswith("/api/rest/v1/categories"):
                    return {"_embedded": {"items": []}}
                if path.startswith("/api/rest/v1/products-uuid"):
                    raise HTTPError(url=path, code=404, msg="Not Found", hdrs=None, fp=None)
                if path.startswith("/api/rest/v1/products"):
                    return {"_embedded": {"items": [{"identifier": "SKU-X"}]}}
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter.sync_inbound()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.summary["domains"].get("Product"), 1)

    def test_akeneo_live_sync_falls_back_to_uuid_then_marker(self):
        """Akeneo CE 7+ allows products without `identifier` (UUID-only).
        The sync must keep them — fall back to `uuid`, then to the
        [retail-os:<sku>] description marker. Dropping such rows would
        silently truncate the cache.
        """
        from app.integrations.systems import AkeneoAdapter
        from app.integrations import store

        os.environ["AKENEO_BASE_URL"] = "http://stub"
        os.environ["AKENEO_CLIENT_ID"] = "cid"
        os.environ["AKENEO_SECRET"] = "sec"
        os.environ["AKENEO_USERNAME"] = "admin"
        os.environ["AKENEO_PASSWORD"] = "pw"

        adapter = AkeneoAdapter()
        adapter._admin_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        # Three products: classic identifier, UUID-only, marker-only.
        products = [
            {"identifier": "SKU-A"},
            {"identifier": None, "uuid": "uuid-b-1234"},
            {
                "identifier": None,
                "uuid": None,
                "values": {
                    "description": [
                        {"locale": "en_US", "data": "[retail-os:SKU-C] fallback path"}
                    ]
                },
            },
        ]

        class _Stub:
            def request(self, path, method="GET", payload=None):
                if path.startswith("/api/rest/v1/categories"):
                    return {"_embedded": {"items": []}}
                if path.startswith("/api/rest/v1/products"):
                    return {"_embedded": {"items": products}}
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter.sync_inbound()
        self.assertEqual(result.status, "success")
        # All three products must have landed.
        self.assertEqual(result.summary["domains"].get("Product"), 3)

        # Cache rows: external_id should be SKU-A, uuid-b-1234, SKU-C.
        bundle = store.list_records(system_id="akeneo", domain="Product", limit=10)
        ext_ids = {r.get("external_id") for r in bundle["records"]}
        self.assertEqual(ext_ids, {"SKU-A", "uuid-b-1234", "SKU-C"})

    def test_akeneo_apply_pim_enrich_patches_product(self):
        from app.integrations.systems import AkeneoAdapter
        from app.integrations import store

        os.environ["AKENEO_BASE_URL"] = "http://stub"
        os.environ["AKENEO_CLIENT_ID"] = "cid"
        os.environ["AKENEO_SECRET"] = "sec"
        os.environ["AKENEO_USERNAME"] = "admin"
        os.environ["AKENEO_PASSWORD"] = "pw"

        adapter = AkeneoAdapter()
        adapter._admin_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        row = store.create_outbox_action(
            system_id="akeneo",
            action_queue_id=None,
            agent="Merchandiser",
            action_type="pim_enrich",
            title="Enrich SKU-001",
            external_domain="Product",
            payload={
                "sku": "SKU-001",
                "values": {
                    "name": [{"locale": "en_US", "scope": None, "data": "Updated name"}],
                },
            },
            configured=True,
        )

        captured: dict = {}

        class _Stub:
            def request(self, path, method="GET", payload=None):
                captured["path"] = path
                captured["method"] = method
                captured["payload"] = payload
                return {}

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertEqual(result["external_id"], "SKU-001")
        self.assertEqual(captured["method"], "PATCH")
        self.assertIn("/api/rest/v1/products/SKU-001", captured["path"])
        self.assertEqual(captured["payload"]["identifier"], "SKU-001")

    def test_akeneo_apply_missing_sku_lands_in_error(self):
        from app.integrations.systems import AkeneoAdapter
        from app.integrations import store

        os.environ["AKENEO_BASE_URL"] = "http://stub"
        os.environ["AKENEO_CLIENT_ID"] = "cid"
        os.environ["AKENEO_SECRET"] = "sec"
        os.environ["AKENEO_USERNAME"] = "admin"
        os.environ["AKENEO_PASSWORD"] = "pw"

        adapter = AkeneoAdapter()
        adapter._admin_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        row = store.create_outbox_action(
            system_id="akeneo",
            action_queue_id=None,
            agent="Merchandiser",
            action_type="pim_enrich",
            title="Bad payload",
            external_domain="Product",
            payload={"values": {"name": []}},  # no sku
            configured=True,
        )
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "error")
        self.assertIn("sku", result["result"]["error"].lower())

    def test_openboxes_apply_unsupported_falls_back(self):
        """`store_transfer` (declared in outbound_domain but NOT in
        LIVE_ACTION_TYPES for OpenBoxes) falls back to base mock-apply.
        """
        from app.integrations.systems import OpenBoxesAdapter
        from app.integrations import store

        os.environ["OPENBOXES_BASE_URL"] = "http://stub"
        os.environ["OPENBOXES_API_TOKEN"] = "tok"

        adapter = OpenBoxesAdapter()
        row = store.create_outbox_action(
            system_id="openboxes",
            action_queue_id=None,
            agent="Merchandiser",
            action_type="store_transfer",
            title="Skip me",
            external_domain="Stock Movement",
            payload={"category": "x"},
            configured=True,
        )
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created")  # base mock-apply

    def test_mautic_list_flags_truncation_when_max_rows_hit(self):
        """If a caller does pass an explicit max_rows and the API has more
        rows than that, the helper must return truncated=True. The default
        path through `_live_sync` passes max_rows=None (no cap) — but if a
        future env knob ever lowers it, the truncation is loud rather than
        silent.
        """
        from app.integrations.systems import MauticAdapter

        adapter = MauticAdapter()
        all_rows = [{"id": str(i)} for i in range(1, 11)]  # 10 rows

        class _BigClient:
            def request(self, path: str):
                qs = path.split("?", 1)[1]
                params = dict(p.split("=", 1) for p in qs.split("&"))
                start = int(params["start"])
                limit = int(params["limit"])
                page = all_rows[start : start + limit]
                return {"lists": {r["id"]: r for r in page}, "total": len(all_rows)}

        adapter._client = lambda: _BigClient()  # type: ignore[method-assign]
        out, truncated = adapter._mautic_list(
            "segments", "lists", limit=3, max_rows=5
        )
        self.assertEqual(len(out), 5)
        self.assertTrue(truncated)

    def test_coerce_id_rejects_missing_and_blank(self):
        """Pin the malformed-id guard: a missing/blank Mautic id must NOT
        cache rows under str(None) == "None" — otherwise multiple bad rows
        collide on a fake external_id and corrupt the cache.
        """
        from app.integrations.systems import _coerce_id

        self.assertIsNone(_coerce_id(None))
        self.assertIsNone(_coerce_id(""))
        self.assertIsNone(_coerce_id("   "))
        self.assertIsNone(_coerce_id(True))   # booleans are not valid ids
        self.assertEqual(_coerce_id(42), "42")
        self.assertEqual(_coerce_id("42"), "42")
        self.assertEqual(_coerce_id("  abc  "), "abc")

    def test_superset_configured_requires_username_password(self):
        from app.integrations.systems import SupersetAdapter

        os.environ["SUPERSET_BASE_URL"] = "http://stub"
        for k in ("SUPERSET_USERNAME", "SUPERSET_PASSWORD"):
            os.environ.pop(k, None)
        self.assertFalse(SupersetAdapter().configured())

        os.environ["SUPERSET_USERNAME"] = "admin"
        self.assertFalse(SupersetAdapter().configured())  # missing password

        os.environ["SUPERSET_PASSWORD"] = "pw"
        self.assertTrue(SupersetAdapter().configured())

    def test_superset_api_list_walks_until_count_consumed(self):
        """Superset paginates via `?q=(page:N,page_size:M)`. Walker must
        stop when accumulated len reaches `count`.
        """
        from app.integrations.systems import SupersetAdapter

        os.environ["SUPERSET_BASE_URL"] = "http://stub"
        os.environ["SUPERSET_USERNAME"] = "admin"
        os.environ["SUPERSET_PASSWORD"] = "pw"

        adapter = SupersetAdapter()
        adapter._admin_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        pages = {
            "/api/v1/dashboard/?q=(page:0,page_size:100)": {
                "result": [{"id": 1}, {"id": 2}],
                "count": 3,
            },
            "/api/v1/dashboard/?q=(page:1,page_size:100)": {
                "result": [{"id": 3}],
                "count": 3,
            },
        }
        seen: list[str] = []

        class _Stub:
            def request(self, path, method="GET", payload=None):
                seen.append(path)
                return pages.get(path, {})

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        out = adapter._api_list("/api/v1/dashboard/")
        self.assertEqual([r["id"] for r in out], [1, 2, 3])
        self.assertEqual(len(seen), 2)

    def test_superset_admin_request_relogins_on_401(self):
        """If the cached JWT expired mid-session, a 401 must clear the
        token, re-login, and retry once. Mirror of Akeneo / Medusa /
        OpenBoxes shared behaviour.
        """
        from app.integrations.systems import SupersetAdapter
        from urllib.error import HTTPError

        os.environ["SUPERSET_BASE_URL"] = "http://stub"
        os.environ["SUPERSET_USERNAME"] = "admin"
        os.environ["SUPERSET_PASSWORD"] = "pw"

        adapter = SupersetAdapter()
        adapter._admin_token = "stale-token"

        login_calls: list[int] = []
        request_calls: list[str] = []

        def _fake_login() -> str:
            login_calls.append(1)
            adapter._admin_token = f"fresh-{len(login_calls)}"
            return adapter._admin_token

        adapter._login = _fake_login  # type: ignore[method-assign]

        class _FlakyClient:
            def __init__(self, token: str):
                self.token = token

            def request(self, path, method="GET", payload=None):
                request_calls.append(self.token)
                if self.token == "stale-token":
                    raise HTTPError(
                        url=path, code=401, msg="Unauthorized", hdrs=None, fp=None
                    )
                return {"ok": True}

        # Mirror the real _client behaviour: re-login first, then build
        # the http client with the current token (akin to Medusa's pattern).
        def _stubbed_client():
            if not adapter._admin_token:
                adapter._login()
            return _FlakyClient(adapter._admin_token)

        adapter._client = _stubbed_client  # type: ignore[method-assign]
        out = adapter._admin_request("/api/v1/dashboard/?q=(page:0,page_size:1)")
        self.assertEqual(out, {"ok": True})
        self.assertEqual(request_calls, ["stale-token", "fresh-1"])
        self.assertEqual(len(login_calls), 1)

    def test_superset_live_sync_caches_dashboard_with_slug_local_id(self):
        """Dashboards round-trip with slug as local_id (preferred over title)."""
        from app.integrations.systems import SupersetAdapter
        from app.integrations import store

        os.environ["SUPERSET_BASE_URL"] = "http://stub"
        os.environ["SUPERSET_USERNAME"] = "admin"
        os.environ["SUPERSET_PASSWORD"] = "pw"

        adapter = SupersetAdapter()
        adapter._admin_token = "tok"
        adapter._login = lambda: "tok"  # type: ignore[method-assign]

        empty_page = {"result": [], "count": 0}
        responses = {
            "/api/v1/database/?q=(page:0,page_size:100)": empty_page,
            "/api/v1/dataset/?q=(page:0,page_size:100)": empty_page,
            "/api/v1/chart/?q=(page:0,page_size:100)": empty_page,
            "/api/v1/dashboard/?q=(page:0,page_size:100)": {
                "result": [{
                    "id": 7,
                    "dashboard_title": "AI Retail OS — Demo",
                    "slug": "ai-retail-os-demo",
                }],
                "count": 1,
            },
        }

        class _Stub:
            def request(self, path, method="GET", payload=None):
                return responses.get(path, {})

        adapter._client = lambda: _Stub()  # type: ignore[method-assign]
        result = adapter._live_sync()
        self.assertEqual(result.summary["mode"], "connected")
        self.assertEqual(result.summary["domains"]["Dashboard"], 1)

        bundle = store.list_records(system_id="superset", domain="Dashboard", limit=10)
        local_ids = {r.get("local_id") for r in bundle["external_refs"]}
        self.assertIn("ai-retail-os-demo", local_ids)

    def test_superset_apply_outbound_falls_back_to_base(self):
        """Superset is read-only: any action_type must hit the base
        adapter (LIVE_ACTION_TYPES is empty by design).
        """
        from app.integrations.systems import SupersetAdapter
        from app.integrations import store

        os.environ["SUPERSET_BASE_URL"] = "http://stub"
        os.environ["SUPERSET_USERNAME"] = "admin"
        os.environ["SUPERSET_PASSWORD"] = "pw"

        adapter = SupersetAdapter()
        row = store.create_outbox_action(
            system_id="superset",
            action_queue_id=None,
            agent="Analyst",
            action_type="report_publish",  # arbitrary; nothing should override
            title="Anything",
            external_domain="Dashboard",
            payload={},
            configured=True,
        )
        result = adapter.apply_outbound(row["id"])
        # Base class: configured + creds → draft_created (no HTTP call).
        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(adapter.LIVE_ACTION_TYPES, set())

    def test_mautic_webhook_is_recorded_as_measurement(self):
        payload = {"campaign_id": "cmp-weekend-heat", "event": "email.open", "count": 12}
        with TestClient(app) as client:
            response = client.post("/api/integrations/mautic/webhook", json=payload)
            records = client.get("/api/integrations/records?system=mautic&domain=Webhook%20Event")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(records.status_code, 200)
        self.assertEqual(records.json()["records"][0]["payload"]["event"], "email.open")

    # --- Shopify (Track 8 P3) ---------------------------------------------

    def test_shopify_configured_requires_domain_and_token(self):
        from app.integrations.systems import ShopifyAdapter

        os.environ.pop("SHOPIFY_SHOP_DOMAIN", None)
        os.environ.pop("SHOPIFY_ADMIN_TOKEN", None)
        self.assertFalse(ShopifyAdapter().configured())

        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        self.assertFalse(ShopifyAdapter().configured())  # missing token

        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"
        self.assertTrue(ShopifyAdapter().configured())

    def test_shopify_gid_tail_extracts_numeric_id(self):
        from app.integrations.systems import _shopify_gid_tail

        self.assertEqual(_shopify_gid_tail("gid://shopify/Product/12345"), "12345")
        self.assertEqual(_shopify_gid_tail("12345"), "12345")
        self.assertIsNone(_shopify_gid_tail(None))
        self.assertIsNone(_shopify_gid_tail(""))

    def test_shopify_gql_list_walks_until_hasNextPage_false(self):
        """`_gql_list` must follow `pageInfo.endCursor` until exhausted —
        a single Shopify connection with more rows than a page would
        otherwise be silently truncated.
        """
        from app.integrations.systems import ShopifyAdapter

        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"

        adapter = ShopifyAdapter()
        pages = [
            {
                "products": {
                    "edges": [{"node": {"id": "gid://shopify/Product/1"}}, {"node": {"id": "gid://shopify/Product/2"}}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                }
            },
            {
                "products": {
                    "edges": [{"node": {"id": "gid://shopify/Product/3"}}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            },
        ]
        seen_cursors: list[str | None] = []

        def _stub_gql(query: str, variables: dict | None = None):
            seen_cursors.append((variables or {}).get("cursor"))
            return pages[len(seen_cursors) - 1]

        adapter._gql = _stub_gql  # type: ignore[method-assign]
        out, truncated = adapter._gql_list("query", "products")
        self.assertEqual([n["id"] for n in out], ["gid://shopify/Product/1", "gid://shopify/Product/2", "gid://shopify/Product/3"])
        self.assertFalse(truncated)
        self.assertEqual(seen_cursors, [None, "c1"])

    def test_shopify_gql_list_flags_truncation_when_max_rows_hit(self):
        from app.integrations.systems import ShopifyAdapter

        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"

        adapter = ShopifyAdapter()
        adapter._gql = lambda q, v=None: {  # type: ignore[method-assign]
            "products": {
                "edges": [
                    {"node": {"id": "gid://shopify/Product/1"}},
                    {"node": {"id": "gid://shopify/Product/2"}},
                    {"node": {"id": "gid://shopify/Product/3"}},
                ],
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            }
        }
        out, truncated = adapter._gql_list("query", "products", max_rows=2)
        self.assertEqual(len(out), 2)
        self.assertTrue(truncated)

    def test_shopify_live_sync_round_trips_metafield_to_external_ref(self):
        """Locations + Products with `retail_os.spine_sku` / `store_id`
        metafields must land an external_ref keyed by the substrate id so
        the cockpit drawer can drill from the substrate row to Shopify.
        """
        from app.integrations import store as adapter_store
        from app.integrations.systems import ShopifyAdapter

        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"

        adapter = ShopifyAdapter()

        def _stub_gql_list(query: str, root_key: str, variables=None, max_rows=None):
            if root_key == "locations":
                return (
                    [
                        {
                            "id": "gid://shopify/Location/91",
                            "name": "NYC store",
                            "metafield": {"value": "sto-nyc"},
                        }
                    ],
                    False,
                )
            if root_key == "products":
                return (
                    [
                        {
                            "id": "gid://shopify/Product/77",
                            "title": "Linen short",
                            "variants": {"edges": [{"node": {"id": "gid://shopify/ProductVariant/770", "sku": "sku-linen-short"}}]},
                            "metafield": {"value": None},
                        }
                    ],
                    False,
                )
            if root_key == "inventoryItems":
                return (
                    [
                        {
                            "id": "gid://shopify/InventoryItem/55",
                            "sku": "sku-linen-short",
                            "inventoryLevels": {
                                "edges": [
                                    {
                                        "node": {
                                            "location": {"id": "gid://shopify/Location/91"},
                                            "quantities": [{"name": "available", "quantity": 12}],
                                        }
                                    }
                                ]
                            },
                        }
                    ],
                    False,
                )
            if root_key == "orders":
                return ([{"id": "gid://shopify/Order/1001", "name": "#1001"}], False)
            return ([], False)

        adapter._gql_list = _stub_gql_list  # type: ignore[method-assign]
        result = adapter._live_sync()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.summary["domains"]["Location"], 1)
        self.assertEqual(result.summary["domains"]["Product"], 1)

        bundle = adapter_store.list_records(system_id="shopify", domain="Location", limit=10)
        recovered = {r.get("local_id") for r in bundle["external_refs"]}
        self.assertIn("sto-nyc", recovered)

        prod_bundle = adapter_store.list_records(system_id="shopify", domain="Product", limit=10)
        recovered_skus = {r.get("local_id") for r in prod_bundle["external_refs"]}
        self.assertIn("sku-linen-short", recovered_skus)

    def test_shopify_live_sync_paginates_nested_inventory_levels(self):
        """Items stocked in >100 locations expose a second page via
        `inventoryLevels.pageInfo.hasNextPage`; the live sync must walk
        that nested connection (not silently truncate) so multi-location
        stores end up with every (item × location) row in record_cache.
        """
        from app.integrations import store as adapter_store
        from app.integrations.systems import ShopifyAdapter

        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"

        adapter = ShopifyAdapter()

        first_page_node = {
            "id": "gid://shopify/InventoryItem/55",
            "sku": "sku-multi-loc",
            "inventoryLevels": {
                "edges": [
                    {
                        "node": {
                            "location": {"id": "gid://shopify/Location/91"},
                            "quantities": [{"name": "available", "quantity": 5}],
                        }
                    }
                ],
                "pageInfo": {"hasNextPage": True, "endCursor": "ic-cursor"},
            },
        }

        def _stub_gql_list(query: str, root_key: str, variables=None, max_rows=None):
            if root_key == "inventoryItems":
                return ([first_page_node], False)
            return ([], False)

        gql_calls: list[dict] = []

        def _stub_gql(query: str, variables=None):
            gql_calls.append({"query": query, "variables": variables})
            return {
                "inventoryItem": {
                    "inventoryLevels": {
                        "edges": [
                            {
                                "node": {
                                    "location": {"id": "gid://shopify/Location/92"},
                                    "quantities": [{"name": "available", "quantity": 3}],
                                }
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }

        adapter._gql_list = _stub_gql_list  # type: ignore[method-assign]
        adapter._gql = _stub_gql  # type: ignore[method-assign]
        result = adapter._live_sync()

        self.assertEqual(result.status, "success")
        self.assertEqual(result.summary["domains"]["Inventory Level"], 2)
        self.assertEqual(len(gql_calls), 1)
        self.assertEqual(gql_calls[0]["variables"]["id"], "gid://shopify/InventoryItem/55")
        self.assertEqual(gql_calls[0]["variables"]["cursor"], "ic-cursor")

        bundle = adapter_store.list_records(system_id="shopify", domain="Inventory Level", limit=20)
        external_ids = {r.get("external_id") for r in bundle["records"]}
        self.assertIn("55:91", external_ids)
        self.assertIn("55:92", external_ids)

    def test_shopify_apply_promotion_creates_automatic_discount(self):
        """promotion → discountAutomaticBasicCreate; external_id is the
        numeric tail of the returned automatic-discount gid.
        """
        from app.integrations import store as adapter_store
        from app.integrations.systems import ShopifyAdapter

        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"
        adapter = ShopifyAdapter()

        captured: dict = {}

        def _stub_gql(query: str, variables=None):
            captured["query"] = query
            captured["variables"] = variables
            return {
                "discountAutomaticBasicCreate": {
                    "automaticDiscountNode": {"id": "gid://shopify/DiscountAutomaticNode/9001"},
                    "userErrors": [],
                }
            }

        adapter._gql = _stub_gql  # type: ignore[method-assign]
        row = adapter_store.create_outbox_action(
            system_id="shopify",
            action_queue_id=None,
            agent="Pricing & Promo",
            action_type="promotion",
            title="Summer apparel 25% off",
            external_domain="Discount",
            payload={"category": "summer_apparel", "discount_pct": 0.25},
            configured=True,
        )
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertEqual(result["external_id"], "9001")
        # Hit the discount mutation, not anything else
        self.assertIn("discountAutomaticBasicCreate", captured["query"])
        # 0.25 → percentage 0.25 (already a fraction; not multiplied to 25)
        pct = captured["variables"]["automaticBasicDiscount"]["customerGets"]["value"]["percentage"]
        self.assertAlmostEqual(pct, 0.25, places=4)

    def test_shopify_apply_fulfillment_routing_stashes_metafield(self):
        """fulfillment_routing → find location by retail_os.store_id metafield,
        append routing entry to retail_os.routing_log, return location external_id.
        """
        from app.integrations import store as adapter_store
        from app.integrations.systems import ShopifyAdapter

        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"
        adapter = ShopifyAdapter()

        gql_calls: list[tuple[str, dict | None]] = []

        def _stub_gql(query: str, variables=None):
            gql_calls.append((query, variables))
            if "LocationByStoreId" in query:
                return {
                    "locations": {
                        "edges": [
                            {
                                "node": {
                                    "id": "gid://shopify/Location/91",
                                    "metafield": {"value": "sto-mia"},
                                    "routingMf": {"value": None},
                                }
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            if "MetafieldsSet" in query or "metafieldsSet" in query:
                return {"metafieldsSet": {"metafields": [{"id": "gid://shopify/Metafield/7"}], "userErrors": []}}
            return {}

        adapter._gql = _stub_gql  # type: ignore[method-assign]
        row = adapter_store.create_outbox_action(
            system_id="shopify",
            action_queue_id=None,
            agent="Fulfillment",
            action_type="fulfillment_routing",
            title="Route Miami BOPIS",
            external_domain="Fulfillment",
            payload={"category": "summer_apparel", "recommended_store": "sto-mia", "guardrail": "uat"},
            configured=True,
        )
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertEqual(result["external_id"], "91")
        # Should have looked up the location and then set the metafield
        kinds = [q for q, _ in gql_calls]
        self.assertTrue(any("LocationByStoreId" in q for q in kinds))
        self.assertTrue(any("metafieldsSet" in q for q in kinds))

    def test_shopify_apply_fulfillment_routing_missing_store_lands_in_error(self):
        from app.integrations import store as adapter_store
        from app.integrations.systems import ShopifyAdapter

        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"
        adapter = ShopifyAdapter()
        adapter._gql = lambda q, v=None: {}  # type: ignore[method-assign]

        row = adapter_store.create_outbox_action(
            system_id="shopify",
            action_queue_id=None,
            agent="Fulfillment",
            action_type="fulfillment_routing",
            title="No target store",
            external_domain="Fulfillment",
            payload={"category": "summer_apparel"},  # no recommended_store
            configured=True,
        )
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "error", msg=result)
        self.assertIn("recommended_store", (result.get("result") or {}).get("message", ""))

    def test_shopify_apply_campaign_brief_stashes_on_shop_metafield(self):
        """Without KLAVIYO_API_KEY the brief lands on a Shop-level metafield.
        external_id is the payload-hash marker so re-apply is a no-op.
        """
        from app.integrations import store as adapter_store
        from app.integrations.systems import ShopifyAdapter

        os.environ.pop("KLAVIYO_API_KEY", None)
        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"
        adapter = ShopifyAdapter()

        def _stub_gql(query: str, variables=None):
            if "ShopId" in query:
                return {"shop": {"id": "gid://shopify/Shop/1"}}
            if "ShopBriefs" in query:
                return {"shop": {"id": "gid://shopify/Shop/1", "metafield": None}}
            if "MetafieldsSet" in query or "metafieldsSet" in query:
                return {"metafieldsSet": {"metafields": [{"id": "gid://shopify/Metafield/8"}], "userErrors": []}}
            return {}

        adapter._gql = _stub_gql  # type: ignore[method-assign]
        row = adapter_store.create_outbox_action(
            system_id="shopify",
            action_queue_id=None,
            agent="Marketing",
            action_type="campaign_brief",
            title="Weekend heatwave",
            external_domain="Campaign",
            payload={"category": "summer_apparel", "segment_id": "seg_vacation"},
            configured=True,
        )
        result = adapter.apply_outbound(row["id"])
        self.assertEqual(result["status"], "draft_created", msg=result)
        # external_id is the payload-hash marker
        self.assertTrue(result["external_id"].startswith("p"))
        details = (result.get("result") or {}).get("details") or {}
        self.assertEqual(details.get("channel"), "shopify-shop-metafield")

    def test_shopify_apply_campaign_brief_klaviyo_passthrough(self):
        """With KLAVIYO_API_KEY set the brief POSTs to Klaviyo instead of
        landing on a Shop metafield.
        """
        from app.integrations import store as adapter_store
        from app.integrations.systems import ShopifyAdapter

        os.environ["KLAVIYO_API_KEY"] = "pk_demo"
        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"
        adapter = ShopifyAdapter()

        captured_klaviyo: dict = {}

        class _StubKlaviyo:
            def request(self, path: str, method: str = "GET", payload=None):
                captured_klaviyo["path"] = path
                captured_klaviyo["method"] = method
                captured_klaviyo["payload"] = payload
                return {"data": {"id": "klv_camp_42", "type": "campaign"}}

        # The Klaviyo path doesn't go through _gql; it constructs a JsonHttpClient.
        # Patch the JsonHttpClient symbol the module imports.
        from app.integrations import systems as systems_mod

        old_client = systems_mod.JsonHttpClient
        systems_mod.JsonHttpClient = lambda *a, **k: _StubKlaviyo()  # type: ignore[assignment]
        try:
            row = adapter_store.create_outbox_action(
                system_id="shopify",
                action_queue_id=None,
                agent="Marketing",
                action_type="campaign_brief",
                title="Weekend heatwave",
                external_domain="Campaign",
                payload={"category": "summer_apparel"},
                configured=True,
            )
            result = adapter.apply_outbound(row["id"])
        finally:
            systems_mod.JsonHttpClient = old_client
            os.environ.pop("KLAVIYO_API_KEY", None)
        self.assertEqual(result["status"], "draft_created", msg=result)
        self.assertEqual(result["external_id"], "klv_camp_42")
        self.assertEqual(captured_klaviyo["path"], "/api/campaigns/")
        self.assertEqual(captured_klaviyo["method"], "POST")
        self.assertEqual((result["result"]["details"] or {}).get("channel"), "klaviyo")

    def test_shopify_apply_unsupported_action_falls_back_to_base(self):
        """store_transfer is not in Shopify's LIVE_ACTION_TYPES; configured
        adapter must hand off to the base mock-apply (draft_created stub
        with no HTTP call) instead of raising.
        """
        from app.integrations import store as adapter_store
        from app.integrations.systems import ShopifyAdapter

        os.environ["SHOPIFY_SHOP_DOMAIN"] = "demo.myshopify.com"
        os.environ["SHOPIFY_ADMIN_TOKEN"] = "shpat_demo"
        adapter = ShopifyAdapter()

        def _explode(*a, **k):
            raise AssertionError("base fallback should not invoke _gql")

        adapter._gql = _explode  # type: ignore[method-assign]
        row = adapter_store.create_outbox_action(
            system_id="shopify",
            action_queue_id=None,
            agent="Merchandiser",
            action_type="store_transfer",
            title="Cross-store transfer",
            external_domain="Transfer",
            payload={"from_store": "sto-chi", "to_store": "sto-mia"},
            configured=True,
        )
        result = adapter.apply_outbound(row["id"])
        # Base class: configured + creds → draft_created (no HTTP call).
        self.assertEqual(result["status"], "draft_created")

    def test_shopify_mock_sync_caches_substrate_skus(self):
        """Without creds the adapter must still produce a useful Records
        payload by mirroring substrate rows — same shape as the other
        adapters' mock paths.
        """
        from app.integrations import store as adapter_store
        from app.integrations.systems import ShopifyAdapter

        os.environ.pop("SHOPIFY_SHOP_DOMAIN", None)
        os.environ.pop("SHOPIFY_ADMIN_TOKEN", None)
        adapter = ShopifyAdapter()
        result = adapter.sync_inbound()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.summary["mode"], "mock")
        self.assertGreater(result.summary["domains"].get("Product", 0), 0)
        bundle = adapter_store.list_records(system_id="shopify", domain="Product", limit=5)
        self.assertGreater(len(bundle["records"]), 0)


if __name__ == "__main__":
    unittest.main()
