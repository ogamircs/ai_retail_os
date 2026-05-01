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
        self.assertEqual(len(systems), 6)
        self.assertIn("erpnext", {system["system_id"] for system in systems})
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

    def test_mautic_webhook_is_recorded_as_measurement(self):
        payload = {"campaign_id": "cmp-weekend-heat", "event": "email.open", "count": 12}
        with TestClient(app) as client:
            response = client.post("/api/integrations/mautic/webhook", json=payload)
            records = client.get("/api/integrations/records?system=mautic&domain=Webhook%20Event")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(records.status_code, 200)
        self.assertEqual(records.json()["records"][0]["payload"]["event"], "email.open")


if __name__ == "__main__":
    unittest.main()
