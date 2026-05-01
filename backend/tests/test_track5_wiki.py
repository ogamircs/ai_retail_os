"""Track 5 — agentic wiki unit tests.

Storage layer (W1), tool builders (W2/W3), Critic-gated auto-publish
(W4), Curator caps (W6).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.agents._mesh_tools import (
    build_wiki_propose_edit_tool,
    build_wiki_read_tool,
    build_wiki_search_tool,
)
from app.spine import db, wiki, events as ev_store
from app.spine.artifacts import write_artifact
from app.substrate import seed


class WikiStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_propose_edit_creates_draft_at_v1(self):
        p = wiki.propose_edit(
            slug="policy/margin_floors",
            title="Margin floors",
            body_md="32% category-wide; 28% on apparel.",
            author_agent="Pricing & Promo",
            refs=["evt-1"],
        )
        self.assertEqual(p.version, 1)
        self.assertEqual(p.status, "draft")
        self.assertEqual(p.refs, ["evt-1"])
        # wiki_edit event landed
        evs = [e for e in ev_store.list_events(limit=10) if e["kind"] == "wiki_edit"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["payload"]["slug"], "policy/margin_floors")

    def test_propose_edit_bumps_version_on_existing_slug(self):
        wiki.propose_edit("x/y", "T", "body 1", "A")
        p2 = wiki.propose_edit("x/y", "T", "body 2", "A")
        self.assertEqual(p2.version, 2)
        self.assertEqual(p2.body_md, "body 2")
        # Both revisions stored
        revs = wiki.list_revisions("x/y")
        self.assertEqual(len(revs), 2)
        self.assertEqual(revs[0]["version"], 2)
        self.assertEqual(revs[1]["version"], 1)

    def test_publish_flips_status_and_emits_event(self):
        wiki.propose_edit("x/y", "T", "b", "A")
        p = wiki.publish_page("x/y", by_agent="Operator")
        self.assertEqual(p.status, "published")
        evs = [e for e in ev_store.list_events(limit=10) if e["kind"] == "wiki_publish"]
        self.assertEqual(len(evs), 1)

    def test_publish_is_idempotent(self):
        wiki.propose_edit("x/y", "T", "b", "A")
        wiki.publish_page("x/y", by_agent="Operator")
        wiki.publish_page("x/y", by_agent="Operator")
        evs = [e for e in ev_store.list_events(limit=10) if e["kind"] == "wiki_publish"]
        # Second call short-circuited — only one publish event total.
        self.assertEqual(len(evs), 1)

    def test_re_edit_after_publish_lands_as_draft(self):
        """A revision on a published page should drop the page back to
        draft so the W4 gate fires before the new body becomes
        operator-visible."""
        wiki.propose_edit("x/y", "T", "v1", "A")
        wiki.publish_page("x/y", "Operator")
        p = wiki.propose_edit("x/y", "T", "v2 - radical change", "A")
        self.assertEqual(p.status, "draft")
        self.assertEqual(p.version, 2)
        self.assertEqual(p.body_md, "v2 - radical change")

    def test_search_pages_matches_title_and_body(self):
        wiki.propose_edit("a/x", "Summer markdowns", "When sell-through < 0.6", "Pricing")
        wiki.propose_edit("b/y", "Vendor reliability", "BreezeCo: 78% on-time", "Replenishment")
        rs = wiki.search_pages("BreezeCo")
        self.assertEqual([p.slug for p in rs], ["b/y"])

    def test_pin_round_trips(self):
        wiki.propose_edit("x/y", "T", "b", "A")
        wiki.publish_page("x/y", "Operator")
        wiki.set_pinned("x/y", True)
        pinned = wiki.list_pinned()
        self.assertEqual([p.slug for p in pinned], ["x/y"])
        wiki.set_pinned("x/y", False)
        self.assertEqual(wiki.list_pinned(), [])

    def test_deprecate_marks_status(self):
        wiki.propose_edit("x/y", "T", "b", "A")
        wiki.publish_page("x/y", "Operator")
        wiki.deprecate_page("x/y", "Operator", reason="superseded")
        p = wiki.get_page("x/y")
        self.assertEqual(p.status, "deprecated")


class WikiToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_search_tool_returns_excerpt_only(self):
        wiki.propose_edit("x/y", "T", "A" * 500, "Author")
        _, search_impl = build_wiki_search_tool()
        out = search_impl({"query": ""})
        self.assertEqual(len(out["results"]), 1)
        self.assertLessEqual(len(out["results"][0]["excerpt"]), 240)

    def test_read_tool_returns_full_body(self):
        wiki.propose_edit("x/y", "T", "FULL BODY", "Author")
        _, read_impl = build_wiki_read_tool()
        out = read_impl({"slug": "x/y"})
        self.assertEqual(out["body_md"], "FULL BODY")

    def test_read_tool_rejects_blank_slug(self):
        _, read_impl = build_wiki_read_tool()
        self.assertIn("error", read_impl({"slug": ""}))
        self.assertIn("error", read_impl({"slug": "  "}))

    def test_read_tool_rejects_unknown_slug(self):
        _, read_impl = build_wiki_read_tool()
        out = read_impl({"slug": "no/such/slug"})
        self.assertIn("error", out)

    def test_propose_tool_writes_as_draft(self):
        _, propose_impl = build_wiki_propose_edit_tool("Pricing & Promo")
        out = propose_impl({
            "slug": "playbook/markdown_summer",
            "title": "T",
            "body_md": "B",
            "refs": ["evt-1", "art-2"],
        })
        self.assertEqual(out["status"], "draft")
        self.assertEqual(out["version"], 1)
        page = wiki.get_page("playbook/markdown_summer")
        self.assertEqual(page.refs, ["evt-1", "art-2"])
        self.assertEqual(page.owner_agent, "Pricing & Promo")

    def test_propose_tool_rejects_missing_slug(self):
        _, propose_impl = build_wiki_propose_edit_tool("Pricing & Promo")
        out = propose_impl({"title": "T", "body_md": "B"})
        self.assertIn("error", out)


class WikiAutoPublishTest(unittest.TestCase):
    """Track 5 W4 — Critic-gated auto-publish runs after a turn and
    promotes drafts to published when the Critic returned no Risks/Gaps.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_clean_critique_promotes_wiki_draft(self):
        """Turn produces:
          - wiki_edit (draft)
          - critique with no Gaps + no Risks
        → auto-publish runs → page is published.
        """
        from app.agents.chief_of_staff import _wiki_auto_publish_clean_drafts
        from datetime import datetime, timezone

        turn_start = datetime.now(timezone.utc).isoformat()
        # Curator emits a draft
        wiki.propose_edit("x/y", "T", "BODY", "Wiki Curator")
        # Clean Critic critique
        critique_id = write_artifact(
            agent="Critic",
            kind="critique",
            title="C",
            body_md=(
                "## Verified\nok\n"
                "## Gaps\n*No material findings.*\n"
                "## Risks\n*No material findings.*\n"
                "## Counter-recommendation\nhold\n"
            ),
            refs=["dummy"],
            stage="critique",
        )
        # Need the observation event so auto-publish discovers the critique
        from app.spine.events import append_event

        append_event(
            agent="Critic",
            kind="observation",
            payload={"artifact_title": "C", "stage": "critique"},
            artifact_id=critique_id,
        )

        _wiki_auto_publish_clean_drafts(turn_start, llm=None)
        page = wiki.get_page("x/y")
        self.assertEqual(page.status, "published")

    def test_dirty_critique_leaves_draft_untouched(self):
        from app.agents.chief_of_staff import _wiki_auto_publish_clean_drafts
        from app.spine.events import append_event
        from datetime import datetime, timezone

        turn_start = datetime.now(timezone.utc).isoformat()
        wiki.propose_edit("x/y", "T", "BODY", "Wiki Curator")
        critique_id = write_artifact(
            agent="Critic",
            kind="critique",
            title="C",
            body_md=(
                "## Verified\nok\n"
                "## Gaps\nMissed the heatwave forecast.\n"
                "## Risks\n*No material findings.*\n"
                "## Counter-recommendation\nrevisit\n"
            ),
            refs=["dummy"],
            stage="critique",
        )
        append_event(
            agent="Critic",
            kind="observation",
            payload={"artifact_title": "C", "stage": "critique"},
            artifact_id=critique_id,
        )
        _wiki_auto_publish_clean_drafts(turn_start, llm=None)
        page = wiki.get_page("x/y")
        # Stayed a draft — operator must approve manually.
        self.assertEqual(page.status, "draft")

    def test_no_critique_leaves_draft_untouched(self):
        """A turn without any Critic involvement (e.g. plain Analyst
        question, no review loop) must not auto-publish wiki drafts.
        """
        from app.agents.chief_of_staff import _wiki_auto_publish_clean_drafts
        from datetime import datetime, timezone

        turn_start = datetime.now(timezone.utc).isoformat()
        wiki.propose_edit("x/y", "T", "BODY", "Wiki Curator")
        _wiki_auto_publish_clean_drafts(turn_start, llm=None)
        page = wiki.get_page("x/y")
        self.assertEqual(page.status, "draft")


class WikiCuratorCapsTest(unittest.TestCase):
    """W6 — per-turn + per-slug rate limits are enforced regardless
    of what the LLM proposes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_per_turn_cap_blocks_fourth_proposal(self):
        from app.agents.wiki_curator import _wiki_propose_with_caps, MAX_PROPOSALS_PER_TURN
        from datetime import datetime, timezone

        turn_start = datetime.now(timezone.utc).isoformat()
        impl = _wiki_propose_with_caps(turn_start)
        out = []
        for i in range(MAX_PROPOSALS_PER_TURN + 2):
            out.append(impl({
                "slug": f"x/y/{i}",
                "title": f"T{i}",
                "body_md": "B",
            }))
        # First N succeed, rest are capped.
        oks = [r for r in out if "slug" in r]
        errs = [r for r in out if "error" in r]
        self.assertEqual(len(oks), MAX_PROPOSALS_PER_TURN)
        self.assertGreaterEqual(len(errs), 1)
        self.assertIn("per-turn cap", errs[0]["error"])

    def test_per_slug_rate_limit_blocks_repeat(self):
        from app.agents.wiki_curator import _wiki_propose_with_caps, NAME
        from datetime import datetime, timedelta, timezone

        # Pretend the Curator already proposed this slug 1h ago.
        recent_ts = datetime.now(timezone.utc).isoformat()
        ev_store.append_event(
            agent=NAME,
            kind="wiki_edit",
            payload={"slug": "policy/margin_floors", "version": 1},
        )
        # New turn starts NOW.
        turn_start = recent_ts
        impl = _wiki_propose_with_caps(turn_start)
        out = impl({
            "slug": "policy/margin_floors",
            "title": "T",
            "body_md": "B",
        })
        self.assertIn("error", out)
        self.assertIn("rate limit", out["error"].lower())


class SpecialistsHaveWikiToolsTest(unittest.TestCase):
    """All 7 action specialists + Critic must expose wiki_search +
    wiki_read. Action specialists also get wiki_propose_edit."""

    def test_action_specialists_have_full_wiki_kit(self):
        from app.agents import (
            analyst,
            fulfillment,
            marketing,
            merchandiser,
            pricing,
            replenishment,
            store_manager,
        )

        for module in (
            analyst,
            fulfillment,
            marketing,
            merchandiser,
            pricing,
            replenishment,
            store_manager,
        ):
            agent = module.build_agent()
            tool_names = {t.name for t in agent.tools}
            self.assertIn("wiki_search", tool_names, f"{agent.name} missing wiki_search")
            self.assertIn("wiki_read", tool_names, f"{agent.name} missing wiki_read")
            self.assertIn(
                "wiki_propose_edit", tool_names, f"{agent.name} missing wiki_propose_edit"
            )

    def test_critic_has_read_only_wiki_kit(self):
        from app.agents import critic

        agent = critic.build_agent()
        tool_names = {t.name for t in agent.tools}
        self.assertIn("wiki_search", tool_names)
        self.assertIn("wiki_read", tool_names)
        # Critic does NOT propose — it audits.
        self.assertNotIn("wiki_propose_edit", tool_names)


if __name__ == "__main__":
    unittest.main()
