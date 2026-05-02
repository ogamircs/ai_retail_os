"""Track 6 — GBrain integration unit tests.

Covers the parts that don't need a live `gbrain serve` instance:

  * `GBrainConfig.from_env` + `GBrainClient` mock-mode dispatch
  * `brain_search` / `brain_read` / `brain_query` tools surface canned
    responses sourced from the wiki when no bearer is configured
  * `code_<kind>` tools return `{mock: true}` so the Critic surfaces
    the gap rather than hallucinating
  * `_fire_brain_ingest` hook records a `brain_ingest` event per turn
    and respects the per-turn page cap
  * `/api/brain/status` + `/api/brain/search` + `/api/brain/pages/<slug>`
    routes — read-only over the mock client

The live HTTP path is env-gated under `RUN_GBRAIN_LIVE=1` because it
needs a running `gbrain serve` instance.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.spine import db, wiki
from app.substrate import seed


class GBrainConfigTest(unittest.TestCase):
    def test_unconfigured_when_env_unset(self):
        from app.llm import mcp

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GBRAIN_BASE_URL", None)
            os.environ.pop("GBRAIN_BEARER", None)
            cfg = mcp.GBrainConfig.from_env()
        self.assertFalse(cfg.configured)

    def test_configured_when_both_set(self):
        from app.llm import mcp

        with mock.patch.dict(
            os.environ,
            {"GBRAIN_BASE_URL": "http://localhost:8787", "GBRAIN_BEARER": "tok"},
            clear=False,
        ):
            cfg = mcp.GBrainConfig.from_env()
        self.assertTrue(cfg.configured)
        self.assertEqual(cfg.base_url, "http://localhost:8787")

    def test_blank_bearer_treated_as_unconfigured(self):
        """An operator who sets `GBRAIN_BEARER=` (empty value) should
        get mock mode, not configured-but-broken."""
        from app.llm import mcp

        with mock.patch.dict(
            os.environ,
            {"GBRAIN_BASE_URL": "http://localhost:8787", "GBRAIN_BEARER": "  "},
            clear=False,
        ):
            cfg = mcp.GBrainConfig.from_env()
        self.assertFalse(cfg.configured)


class GBrainMockClientTest(unittest.TestCase):
    """Mock client must surface `wiki_pages` content so the cockpit's
    demo path is coherent without GBrain installed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()
        # Two published wiki pages give the mock search something to
        # return.
        wiki.propose_edit(
            "policy/margin_floors", "Margin floors", "Stay above 0.20.", "Pricing"
        )
        wiki.publish_page("policy/margin_floors", "Operator")
        wiki.propose_edit(
            "vendor/breezeco/reliability_notes",
            "BreezeCo reliability",
            "78% on-time delivery over Q3.",
            "Replenishment",
        )
        wiki.publish_page("vendor/breezeco/reliability_notes", "Operator")

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()
        from app.llm import mcp

        mcp.reset_client()

    def _client(self):
        from app.llm import mcp

        # Force unconfigured so .search() takes the mock path even if
        # the developer's shell happens to have GBRAIN_BEARER set.
        return mcp.GBrainClient(config=mcp.GBrainConfig(base_url=None, bearer=None))

    def test_search_returns_wiki_pages(self):
        out = self._client().search("BreezeCo")
        self.assertTrue(out.get("mock"))
        slugs = [r["slug"] for r in out.get("results", [])]
        self.assertIn("vendor/breezeco/reliability_notes", slugs)

    def test_get_returns_full_body(self):
        out = self._client().get("policy/margin_floors")
        self.assertTrue(out.get("mock"))
        self.assertEqual(out["slug"], "policy/margin_floors")
        self.assertIn("0.20", out["body"])

    def test_get_unknown_slug_returns_error(self):
        out = self._client().get("nonexistent/slug")
        self.assertTrue(out.get("mock"))
        self.assertIn("error", out)

    def test_query_returns_top_excerpt_and_citations(self):
        out = self._client().query("BreezeCo")
        self.assertTrue(out.get("mock"))
        self.assertIn("answer", out)
        citations = out.get("citations", [])
        self.assertGreaterEqual(len(citations), 1)

    def test_ingest_no_op_in_mock_mode(self):
        out = self._client().ingest([{"slug": "x", "title": "y", "body": "z"}])
        self.assertTrue(out.get("mock"))
        self.assertEqual(out.get("ingested"), 0)

    def test_code_lookup_mock_returns_empty_with_flag(self):
        out = self._client().code_lookup("callers", "publish_page")
        self.assertTrue(out.get("mock"))
        self.assertEqual(out.get("kind"), "callers")
        self.assertEqual(out.get("results"), [])

    def test_code_lookup_rejects_unknown_kind(self):
        out = self._client().code_lookup("invalid_kind", "x")
        self.assertIn("error", out)


class BrainToolBuildersTest(unittest.TestCase):
    """Tool builders must wire to the singleton client via reset/get."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()
        wiki.propose_edit("policy/x", "T", "body", "Pricing")
        wiki.publish_page("policy/x", "Operator")
        from app.llm import mcp

        mcp.reset_client()

    def tearDown(self):
        from app.llm import mcp

        db.DB_PATH = self.old_db
        self.tmp.cleanup()
        mcp.reset_client()

    def test_brain_search_tool_rejects_blank_query(self):
        from app.agents._mesh_tools import build_brain_search_tool

        _, impl = build_brain_search_tool()
        out = impl({"query": ""})
        self.assertIn("error", out)

    def test_brain_read_tool_rejects_blank_slug(self):
        from app.agents._mesh_tools import build_brain_read_tool

        _, impl = build_brain_read_tool()
        out = impl({"slug": ""})
        self.assertIn("error", out)

    def test_code_lookup_tool_rejects_blank_symbol(self):
        from app.agents._mesh_tools import build_code_lookup_tool

        _, impl = build_code_lookup_tool("callers")
        out = impl({"symbol": ""})
        self.assertIn("error", out)

    def test_code_lookup_tool_rejects_unknown_kind_at_build(self):
        from app.agents._mesh_tools import build_code_lookup_tool

        with self.assertRaises(ValueError):
            build_code_lookup_tool("not_a_kind")


class BrainIngestHookTest(unittest.TestCase):
    """G3 — `_fire_brain_ingest` must record a `brain_ingest` spine
    event scoped to the turn id, and respect the 3-pages-per-turn cap."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()
        from app.llm import mcp

        mcp.reset_client()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()
        from app.llm import mcp

        mcp.reset_client()

    def test_ingest_records_brain_ingest_event_in_mock_mode(self):
        from app.agents.chief_of_staff import _fire_brain_ingest
        from app.spine.artifacts import write_artifact
        from app.spine.events import append_event, list_events

        # Stamp the turn_id on artifacts so events_for_turn picks them up.
        with mock.patch(
            "app.spine.events.current_turn_id",
            new=__import__("contextvars").ContextVar(
                "current_turn_id", default="turn-test"
            ),
        ):
            aid = write_artifact(
                agent="Analyst",
                kind="report",
                title="Test artifact",
                body_md="some content",
                refs=[],
                stage="final",
            )
            append_event(
                agent="Analyst",
                kind="observation",
                payload={"stage": "final"},
                artifact_id=aid,
            )
        # Now drive the ingest helper.
        _fire_brain_ingest(
            "operator prompt",
            turn_start_iso="2026-01-01T00:00:00Z",
            turn_id="turn-test",
        )
        # In mock mode the brain_ingest event lands inline (no daemon
        # thread). Look for it.
        events = list_events(limit=50)
        ingests = [e for e in events if e["kind"] == "brain_ingest"]
        self.assertEqual(
            len(ingests), 1, f"expected one brain_ingest, got {ingests}"
        )
        payload = ingests[0]["payload"]
        self.assertEqual(payload["turn_id"], "turn-test")
        self.assertGreaterEqual(payload["pages_attempted"], 1)

    def test_ingest_no_op_when_turn_has_no_artifacts(self):
        from app.agents.chief_of_staff import _fire_brain_ingest
        from app.spine.events import list_events

        _fire_brain_ingest(
            "operator prompt",
            turn_start_iso="2026-01-01T00:00:00Z",
            turn_id="empty-turn",
        )
        events = list_events(limit=50)
        ingests = [e for e in events if e["kind"] == "brain_ingest"]
        self.assertEqual(ingests, [])

    def test_ingest_caps_at_max_pages(self):
        from app.agents import chief_of_staff
        from app.agents.chief_of_staff import _fire_brain_ingest
        from app.spine.artifacts import write_artifact
        from app.spine.events import append_event, list_events

        # Seed 5 artifacts on the same turn — the cap is 3.
        with mock.patch(
            "app.spine.events.current_turn_id",
            new=__import__("contextvars").ContextVar(
                "current_turn_id", default="turn-cap"
            ),
        ):
            for i in range(5):
                aid = write_artifact(
                    agent="Analyst",
                    kind="report",
                    title=f"Test {i}",
                    body_md="body",
                    refs=[],
                    stage="final",
                )
                append_event(
                    agent="Analyst",
                    kind="observation",
                    payload={"i": i},
                    artifact_id=aid,
                )
        _fire_brain_ingest(
            "p", turn_start_iso="2026-01-01T00:00:00Z", turn_id="turn-cap"
        )
        events = list_events(limit=50)
        ingests = [e for e in events if e["kind"] == "brain_ingest"]
        self.assertEqual(len(ingests), 1)
        payload = ingests[0]["payload"]
        self.assertEqual(
            payload["pages_attempted"], chief_of_staff._BRAIN_INGEST_MAX_PAGES
        )


class BrainApiRoutesTest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()
        wiki.propose_edit("policy/api_test", "API test page", "body content", "Pricing")
        wiki.publish_page("policy/api_test", "Operator")
        from app.llm import mcp

        mcp.reset_client()
        self.client = TestClient(app)

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()
        from app.llm import mcp

        mcp.reset_client()

    def test_status_route_reports_mock_when_unconfigured(self):
        # Force unconfigured even if the dev shell has the env set.
        with mock.patch.dict(
            os.environ,
            {"GBRAIN_BASE_URL": "", "GBRAIN_BEARER": ""},
            clear=False,
        ):
            from app.llm import mcp

            mcp.reset_client()
            r = self.client.get("/api/brain/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["configured"])
        self.assertTrue(body["mock"])
        self.assertGreaterEqual(body["pages_count"], 1)

    def test_search_route_returns_results(self):
        with mock.patch.dict(
            os.environ,
            {"GBRAIN_BASE_URL": "", "GBRAIN_BEARER": ""},
            clear=False,
        ):
            from app.llm import mcp

            mcp.reset_client()
            r = self.client.get("/api/brain/search?q=API")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("mock"))
        slugs = [x["slug"] for x in body.get("results", [])]
        self.assertIn("policy/api_test", slugs)

    def test_pages_route_returns_full_body(self):
        with mock.patch.dict(
            os.environ,
            {"GBRAIN_BASE_URL": "", "GBRAIN_BEARER": ""},
            clear=False,
        ):
            from app.llm import mcp

            mcp.reset_client()
            r = self.client.get("/api/brain/pages/policy/api_test")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["slug"], "policy/api_test")
        self.assertIn("body content", body["body"])

    def test_pages_route_404s_unknown_slug(self):
        with mock.patch.dict(
            os.environ,
            {"GBRAIN_BASE_URL": "", "GBRAIN_BEARER": ""},
            clear=False,
        ):
            from app.llm import mcp

            mcp.reset_client()
            r = self.client.get("/api/brain/pages/never/heard/of/it")
        self.assertEqual(r.status_code, 404)


@unittest.skipUnless(
    os.getenv("RUN_GBRAIN_LIVE"),
    "set RUN_GBRAIN_LIVE=1 + GBRAIN_BASE_URL + GBRAIN_BEARER to run live",
)
class GBrainLiveTest(unittest.TestCase):
    """Env-gated live test. Requires `gbrain serve --http --port 8787`
    running locally with a valid bearer token."""

    def test_live_search_returns_dict(self):
        from app.llm.mcp import GBrainClient

        client = GBrainClient()
        out = client.search("test", limit=5)
        self.assertIsInstance(out, dict)
        # Live mode never sets `mock`, only mock fallback does.
        self.assertNotIn("mock", out)


if __name__ == "__main__":
    unittest.main()
