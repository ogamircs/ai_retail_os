"""Track 8 — Improvement Auditor unit tests.

Covers the parts that don't need a live LLM:

  * `gather_signals` — surfaces flagged categories, integrations,
    wiki coverage, recent events
  * `create_run` / `complete_run` / `record_suggestion` /
    `update_suggestion_status` round-trip through the schema
  * `list_suggestions` filter by status / run_id
  * record_suggestion tool input validation
  * `MAX_SUGGESTIONS_PER_RUN` cap
  * /api/improvements/* routes — POST run kicks off background
    thread (worker patched), GET /runs / GET /suggestions /
    accept / dismiss

The full end-to-end LLM pass is env-gated under `RUN_AUDIT_LIVE=1`.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.spine import db, wiki
from app.substrate import seed


class GatherSignalsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_signals_surface_flagged_categories(self):
        from app.agents.improvement_auditor import gather_signals

        sigs = gather_signals()
        self.assertIn("categories_flagged", sigs)
        self.assertIn("categories_total", sigs)
        # The seed puts at least one category in late-season clearance
        # / overstock — should land in the flagged list.
        self.assertGreaterEqual(len(sigs["categories_flagged"]), 1)

    def test_signals_include_integrations_summary(self):
        from app.agents.improvement_auditor import gather_signals

        sigs = gather_signals()
        self.assertIn("integrations", sigs)
        # 6 adapters shipped in Track 1.
        slugs = [s["system_id"] for s in sigs["integrations"]]
        for required in ("erpnext", "mautic", "medusa", "openboxes", "akeneo", "superset"):
            self.assertIn(required, slugs)

    def test_signals_compute_wiki_coverage(self):
        """A category without a wiki page should land in coverage_gaps."""
        from app.agents.improvement_auditor import gather_signals

        sigs = gather_signals()
        self.assertIn("wiki", sigs)
        self.assertIn("coverage_gaps", sigs["wiki"])
        # No wiki pages exist post-seed → every flagged category is a
        # coverage gap.
        if sigs.get("categories_flagged"):
            self.assertGreaterEqual(len(sigs["wiki"]["coverage_gaps"]), 1)

    def test_signals_have_no_top_level_errors(self):
        """Smoke test — gather_signals shouldn't raise on a fresh seed."""
        from app.agents.improvement_auditor import gather_signals

        sigs = gather_signals()
        # Errors are caught + reported under `<area>_error` keys —
        # surfacing them tells us the helper is broken even if the
        # outer call returns.
        error_keys = [k for k in sigs if k.endswith("_error")]
        self.assertEqual(error_keys, [], f"unexpected errors: {error_keys}")


class RunLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_create_then_complete_run(self):
        from app.agents.improvement_auditor import create_run, complete_run, get_run

        create_run("r1")
        run = get_run("r1")
        self.assertEqual(run["status"], "running")
        self.assertIsNone(run["ended_ts"])

        complete_run("r1", {"suggestions": 3, "elapsed_s": 4.2})
        run = get_run("r1")
        self.assertEqual(run["status"], "ok")
        self.assertIsNotNone(run["ended_ts"])
        self.assertEqual(run["summary"]["suggestions"], 3)

    def test_complete_run_with_error_marks_status(self):
        from app.agents.improvement_auditor import create_run, complete_run, get_run

        create_run("r2")
        complete_run("r2", {"phase": "agent"}, error="LLM failed")
        run = get_run("r2")
        self.assertEqual(run["status"], "error")
        self.assertEqual(run["error"], "LLM failed")

    def test_list_runs_orders_newest_first(self):
        from app.agents.improvement_auditor import create_run, list_runs

        create_run("r-a")
        create_run("r-b")
        runs = list_runs(limit=10)
        self.assertEqual(runs[0]["id"], "r-b")
        self.assertEqual(runs[1]["id"], "r-a")


class SuggestionLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_record_then_filter_open(self):
        from app.agents.improvement_auditor import (
            create_run,
            list_suggestions,
            record_suggestion,
        )

        create_run("r1")
        record_suggestion(
            "r1",
            area="pricing",
            severity="high",
            title="Markdown summer apparel",
            body_md="- WoW -22%; - Tier 1 30%.",
            action_hint="propose_outbox",
        )
        record_suggestion(
            "r1",
            area="wiki_coverage",
            severity="low",
            title="Add summer markdown playbook",
            body_md="- coverage gap on category/summer_apparel.",
            action_hint="wiki_edit",
        )
        opens = list_suggestions(run_id="r1", status="open")
        self.assertEqual(len(opens), 2)

    def test_accept_then_filtered_out_of_open(self):
        from app.agents.improvement_auditor import (
            create_run,
            list_suggestions,
            record_suggestion,
            update_suggestion_status,
        )

        create_run("r1")
        s = record_suggestion(
            "r1", area="pricing", severity="high",
            title="t", body_md="b",
        )
        update_suggestion_status(s["id"], "accepted")
        self.assertEqual(list_suggestions(run_id="r1", status="open"), [])
        accepted = list_suggestions(run_id="r1", status="accepted")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["status"], "accepted")

    def test_update_rejects_unknown_status(self):
        from app.agents.improvement_auditor import (
            create_run,
            record_suggestion,
            update_suggestion_status,
        )

        create_run("r1")
        s = record_suggestion("r1", area="pricing", severity="high", title="t", body_md="b")
        with self.assertRaises(ValueError):
            update_suggestion_status(s["id"], "bogus")


class RecordSuggestionToolTest(unittest.TestCase):
    """The agent's only tool — must validate severity / area / action_hint
    + cap at MAX_SUGGESTIONS_PER_RUN."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_tool_rejects_invalid_severity(self):
        from app.agents.improvement_auditor import (
            _build_record_suggestion_tool,
            create_run,
        )

        create_run("r1")
        _, impl, _ = _build_record_suggestion_tool("r1")
        out = impl(
            {
                "area": "pricing",
                "severity": "URGENT",
                "title": "t",
                "body_md": "b",
            }
        )
        self.assertIn("error", out)
        self.assertIn("severity", out["error"])

    def test_tool_rejects_invalid_area(self):
        from app.agents.improvement_auditor import (
            _build_record_suggestion_tool,
            create_run,
        )

        create_run("r1")
        _, impl, _ = _build_record_suggestion_tool("r1")
        out = impl(
            {
                "area": "bogus_area",
                "severity": "high",
                "title": "t",
                "body_md": "b",
            }
        )
        self.assertIn("error", out)
        self.assertIn("area", out["error"])

    def test_tool_caps_at_max_per_run(self):
        from app.agents.improvement_auditor import (
            MAX_SUGGESTIONS_PER_RUN,
            _build_record_suggestion_tool,
            create_run,
        )

        create_run("r1")
        _, impl, counter = _build_record_suggestion_tool("r1")
        for i in range(MAX_SUGGESTIONS_PER_RUN):
            out = impl(
                {
                    "area": "pricing",
                    "severity": "low",
                    "title": f"t{i}",
                    "body_md": f"body {i}",
                }
            )
            self.assertIn("suggestion_id", out)
        # N+1 must error out.
        out = impl(
            {
                "area": "pricing",
                "severity": "low",
                "title": "extra",
                "body_md": "body extra",
            }
        )
        self.assertIn("error", out)
        self.assertEqual(counter["n"], MAX_SUGGESTIONS_PER_RUN)


class ImprovementsApiRoutesTest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()
        wiki.propose_edit("policy/x", "T", "b", "Pricing")
        wiki.publish_page("policy/x", "Operator")
        self.client = TestClient(app)

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_signals_route_returns_dict(self):
        r = self.client.get("/api/improvements/signals")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("integrations", body)
        self.assertIn("categories_flagged", body)

    def test_run_route_kicks_off_background_job(self):
        import time

        from app.agents import improvement_auditor as ia

        def _fake_audit(run_id, llm):
            ia.record_suggestion(
                run_id=run_id,
                area="pricing",
                severity="high",
                title="patched",
                body_md="patched body",
            )
            ia.complete_run(run_id, {"suggestions": 1, "elapsed_s": 0.01})
            return {"status": "ok"}

        # Patch survives the duration of the background thread —
        # exiting the `with` before the thread finishes would let the
        # real (LLM-driven) run_audit fire and break the test.
        with mock.patch.object(ia, "run_audit", _fake_audit):
            r = self.client.post("/api/improvements/run")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertIn("run_id", body)
            self.assertEqual(body["status"], "running")

            # Daemon thread runs concurrently; poll up to ~2s for terminal.
            run = None
            for _ in range(40):
                run = self.client.get(
                    f"/api/improvements/runs/{body['run_id']}"
                ).json()
                if run["status"] != "running":
                    break
                time.sleep(0.05)
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["summary"]["suggestions"], 1)

    def test_suggestions_filter_dispatch(self):
        from app.agents.improvement_auditor import create_run, record_suggestion

        create_run("rx")
        record_suggestion("rx", area="pricing", severity="high", title="a", body_md="b")
        # Default status=open
        r = self.client.get("/api/improvements/suggestions")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["suggestions"]), 1)
        # status=all returns everything regardless of status.
        r2 = self.client.get("/api/improvements/suggestions?status=all")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(r2.json()["suggestions"]), 1)

    def test_accept_then_dismiss_flow(self):
        from app.agents.improvement_auditor import create_run, record_suggestion

        create_run("rx")
        s = record_suggestion(
            "rx", area="pricing", severity="high", title="t", body_md="b"
        )
        r = self.client.post(f"/api/improvements/suggestions/{s['id']}/accept")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "accepted")

        r2 = self.client.post(f"/api/improvements/suggestions/{s['id']}/dismiss")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["status"], "dismissed")

    def test_unknown_run_id_404(self):
        r = self.client.get("/api/improvements/runs/nonexistent")
        self.assertEqual(r.status_code, 404)

    def test_unknown_suggestion_id_404(self):
        r = self.client.post("/api/improvements/suggestions/99999/accept")
        self.assertEqual(r.status_code, 404)


@unittest.skipUnless(
    os.getenv("RUN_AUDIT_LIVE"),
    "set RUN_AUDIT_LIVE=1 + an LLM API key to drive a real audit run",
)
class ImprovementAuditorLiveTest(unittest.TestCase):
    """Env-gated live test. Requires a real LLM API key."""

    def test_live_audit_records_suggestions(self):
        import uuid

        from app.agents.improvement_auditor import (
            create_run,
            list_suggestions,
            run_audit,
        )
        from app.llm import get_provider

        run_id = uuid.uuid4().hex[:12]
        create_run(run_id)
        out = run_audit(run_id, get_provider())
        self.assertIn(out["status"], ("ok", "error"))
        if out["status"] == "ok":
            ss = list_suggestions(run_id=run_id, status="open")
            self.assertGreaterEqual(len(ss), 1)


if __name__ == "__main__":
    unittest.main()
