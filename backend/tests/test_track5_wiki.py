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

    def test_api_pages_status_empty_returns_all_stages(self):
        """status='' on /api/wiki/pages must bypass the default
        published-only filter so the cockpit's 'all stages' toggle
        actually returns drafts + deprecated."""
        from fastapi.testclient import TestClient
        from app.main import app

        wiki.propose_edit("d/x", "Draft only", "b", "A")  # draft, never published
        wiki.propose_edit("p/y", "Published one", "b", "A")
        wiki.publish_page("p/y", "Operator")

        c = TestClient(app)
        r_default = c.get("/api/wiki/pages")  # default: status=published
        slugs_pub = {p["slug"] for p in r_default.json()["pages"]}
        self.assertIn("p/y", slugs_pub)
        self.assertNotIn("d/x", slugs_pub)

        r_all = c.get("/api/wiki/pages?status=")  # explicit empty = all stages
        slugs_all = {p["slug"] for p in r_all.json()["pages"]}
        self.assertIn("p/y", slugs_all)
        self.assertIn("d/x", slugs_all)

    def test_api_search_honours_status_filter(self):
        """Searching with status=draft must hide published matches."""
        from fastapi.testclient import TestClient
        from app.main import app

        wiki.propose_edit("d/foo", "FOOTITLE", "body about FOO", "A")  # draft
        wiki.propose_edit("p/foo", "FOOTITLE pub", "body about FOO too", "A")
        wiki.publish_page("p/foo", "Operator")

        c = TestClient(app)
        r_draft = c.get("/api/wiki/search?q=FOO&status=draft")
        slugs_draft = {p["slug"] for p in r_draft.json()["pages"]}
        self.assertEqual(slugs_draft, {"d/foo"})

        r_pub = c.get("/api/wiki/search?q=FOO&status=published")
        slugs_pub = {p["slug"] for p in r_pub.json()["pages"]}
        self.assertEqual(slugs_pub, {"p/foo"})

        # No status filter (or empty) returns both.
        r_all = c.get("/api/wiki/search?q=FOO&status=")
        slugs_all = {p["slug"] for p in r_all.json()["pages"]}
        self.assertEqual(slugs_all, {"d/foo", "p/foo"})

    def test_deprecate_marks_status(self):
        wiki.propose_edit("x/y", "T", "b", "A")
        wiki.publish_page("x/y", "Operator")
        wiki.deprecate_page("x/y", "Operator", reason="superseded")
        p = wiki.get_page("x/y")
        self.assertEqual(p.status, "deprecated")

    def test_deprecate_updates_latest_revision_status(self):
        """list_revisions must report the latest revision as
        'deprecated' after deprecate_page — otherwise audit consumers
        walking revisions see stale 'published' / 'draft' status."""
        wiki.propose_edit("x/y", "T", "b", "A")
        wiki.publish_page("x/y", "Operator")
        # Latest revision is published before deprecation.
        revs_before = wiki.list_revisions("x/y")
        self.assertEqual(revs_before[0]["status"], "published")

        wiki.deprecate_page("x/y", "Operator")
        revs_after = wiki.list_revisions("x/y")
        self.assertEqual(revs_after[0]["status"], "deprecated")


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
        wiki.publish_page("x/y", "Operator")
        _, search_impl = build_wiki_search_tool()
        out = search_impl({"query": ""})
        self.assertEqual(len(out["results"]), 1)
        self.assertLessEqual(len(out["results"][0]["excerpt"]), 240)

    def test_search_tool_defaults_to_published(self):
        """Drafts must NOT surface in agent search by default —
        otherwise unreviewed knowledge leaks back into agent context.
        """
        wiki.propose_edit("d/x", "Draft", "BODY", "Author")  # never published
        wiki.propose_edit("p/y", "Pub", "BODY", "Author")
        wiki.publish_page("p/y", "Operator")
        _, search_impl = build_wiki_search_tool()
        out = search_impl({"query": ""})
        slugs = {r["slug"] for r in out["results"]}
        self.assertEqual(slugs, {"p/y"})
        # Explicit opt-in for "all" surfaces drafts too.
        out_all = search_impl({"query": "", "status": "all"})
        self.assertEqual({r["slug"] for r in out_all["results"]}, {"d/x", "p/y"})

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
          - Pricing draft artifact (the agent draft being reviewed)
          - Critic critique referencing it; clean (no Gaps + no Risks)
          - Pricing wiki_edit
        → auto-publish runs → wiki page is published because Pricing's
        own draft was the reviewed-clean target.
        """
        from app.agents.chief_of_staff import _wiki_auto_publish_clean_drafts
        from datetime import datetime, timezone
        from app.spine.events import append_event

        turn_start = datetime.now(timezone.utc).isoformat()
        # Pricing's draft (the artifact being audited)
        pricing_draft_id = write_artifact(
            agent="Pricing & Promo", kind="plan", title="Markdown",
            body_md="markdown 25%", refs=[], stage="draft",
        )
        # Pricing emits the wiki edit; refs back to its reviewed draft
        wiki.propose_edit(
            "x/y", "T", "BODY", "Pricing & Promo", refs=[pricing_draft_id]
        )
        # Critic critique referencing the Pricing draft, clean
        critique_id = write_artifact(
            agent="Critic", kind="critique", title="C",
            body_md=(
                "## Verified\nok\n"
                "## Gaps\n*No material findings.*\n"
                "## Risks\n*No material findings.*\n"
                "## Counter-recommendation\nhold\n"
            ),
            refs=[pricing_draft_id], stage="critique",
        )
        append_event(
            agent="Critic", kind="observation",
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

    def test_mixed_critiques_one_dirty_blocks_publish(self):
        """Multi-specialist turn: one critique clean, one dirty.
        Auto-publish must hold the wiki draft — a single dirty
        critique anywhere in the turn signals an unvetted draft.
        """
        from app.agents.chief_of_staff import _wiki_auto_publish_clean_drafts
        from app.spine.events import append_event
        from datetime import datetime, timezone

        turn_start = datetime.now(timezone.utc).isoformat()
        wiki.propose_edit("x/y", "T", "BODY", "Wiki Curator")

        clean_id = write_artifact(
            agent="Critic", kind="critique", title="C1",
            body_md=(
                "## Verified\nok\n## Gaps\n*No material findings.*\n"
                "## Risks\n*No material findings.*\n## Counter-recommendation\nhold\n"
            ),
            refs=["x"], stage="critique",
        )
        dirty_id = write_artifact(
            agent="Critic", kind="critique", title="C2",
            body_md=(
                "## Verified\nok\n## Gaps\nMissed weather forecast.\n"
                "## Risks\n*No material findings.*\n## Counter-recommendation\nrevisit\n"
            ),
            refs=["x"], stage="critique",
        )
        for cid in (clean_id, dirty_id):
            append_event(
                agent="Critic", kind="observation",
                payload={"artifact_title": "C", "stage": "critique"},
                artifact_id=cid,
            )

        _wiki_auto_publish_clean_drafts(turn_start, llm=None)
        page = wiki.get_page("x/y")
        # ALL critiques must be clean → mixed turn keeps draft.
        self.assertEqual(page.status, "draft")

    def test_event_id_refs_resolve_to_reviewed_artifact(self):
        """wiki_propose_edit refs may be event IDs or artifact IDs.
        Auto-publish must resolve event-id refs to the linked
        artifact id and treat that as reviewed-proof. Without this,
        citing only event ids forced manual approval even when the
        linked artifact had been critiqued."""
        from app.agents.chief_of_staff import _wiki_auto_publish_clean_drafts
        from app.spine.events import append_event, current_turn_id
        from datetime import datetime, timezone

        turn_start = datetime.now(timezone.utc).isoformat()
        token = current_turn_id.set("turn-evtref")
        try:
            pricing_draft_id = write_artifact(
                agent="Pricing & Promo", kind="plan", title="P",
                body_md="b", refs=[], stage="draft",
            )
            # Spine event linking to the Pricing draft artifact
            ev_id = append_event(
                agent="Pricing & Promo", kind="proposal",
                payload={"artifact_title": "P"},
                artifact_id=pricing_draft_id,
            )
            critique_id = write_artifact(
                agent="Critic", kind="critique", title="C",
                body_md=(
                    "## Verified\nok\n## Gaps\n*No material findings.*\n"
                    "## Risks\n*No material findings.*\n## Counter-recommendation\nhold\n"
                ),
                refs=[pricing_draft_id], stage="critique",
            )
            append_event(
                agent="Critic", kind="observation",
                payload={"artifact_title": "C"}, artifact_id=critique_id,
            )
            # wiki_edit cites the EVENT id (not the artifact id).
            wiki.propose_edit(
                "policy/evt", "T", "BODY", "Pricing & Promo",
                refs=[str(ev_id)],
            )
        finally:
            current_turn_id.reset(token)

        _wiki_auto_publish_clean_drafts(
            turn_start, llm=None, turn_id="turn-evtref"
        )
        # Event ref resolved to the reviewed Pricing artifact → publish.
        self.assertEqual(wiki.get_page("policy/evt").status, "published")

    def test_unreviewed_re_edit_blocks_publish_even_when_earlier_edit_was_reviewed(self):
        """Same slug edited twice in one turn:
          - v1 wiki_edit refs the reviewed Pricing draft
          - v2 wiki_edit (unreviewed re-edit) overwrites with new body
        Auto-publish must NOT publish the page — the body the
        operator would see is the unreviewed v2.
        """
        from app.agents.chief_of_staff import _wiki_auto_publish_clean_drafts
        from app.spine.events import append_event, current_turn_id
        from datetime import datetime, timezone

        turn_start = datetime.now(timezone.utc).isoformat()
        token = current_turn_id.set("turn-reedit")
        try:
            pricing_draft_id = write_artifact(
                agent="Pricing & Promo", kind="plan", title="P",
                body_md="b", refs=[], stage="draft",
            )
            critique_id = write_artifact(
                agent="Critic", kind="critique", title="C",
                body_md=(
                    "## Verified\nok\n## Gaps\n*No material findings.*\n"
                    "## Risks\n*No material findings.*\n## Counter-recommendation\nhold\n"
                ),
                refs=[pricing_draft_id], stage="critique",
            )
            append_event(
                agent="Critic", kind="observation",
                payload={"artifact_title": "C"}, artifact_id=critique_id,
            )
            # First edit refs the reviewed draft
            wiki.propose_edit(
                "policy/reedit", "T", "VERSION 1", "Pricing & Promo",
                refs=[pricing_draft_id],
            )
            # Same agent re-edits the same slug — but cites no reviewed
            # artifact. The page body now reflects the unreviewed v2.
            wiki.propose_edit(
                "policy/reedit", "T", "VERSION 2 unreviewed", "Pricing & Promo",
                refs=["unrelated-evt"],
            )
        finally:
            current_turn_id.reset(token)

        _wiki_auto_publish_clean_drafts(turn_start, llm=None, turn_id="turn-reedit")
        page = wiki.get_page("policy/reedit")
        # Latest edit was unreviewed → page must stay draft.
        self.assertEqual(page.status, "draft")
        self.assertEqual(page.body_md, "VERSION 2 unreviewed")

    def test_run_chief_resets_turn_id_even_when_chief_raises(self):
        """current_turn_id contextvar must be cleared even if
        chief.run raises mid-stream — otherwise the next request on
        the same worker mis-tags its append_event calls.
        """
        from app.agents import chief_of_staff
        from app.spine.events import current_turn_id

        # Sanity: contextvar starts empty.
        self.assertEqual(current_turn_id.get(), "")

        class _RaisingLLM:
            name = "stub"
            model = "stub-1"

            def chat(self, *_a, **_kw):
                raise RuntimeError("boom mid-turn")

        # Drain the generator — should propagate / yield error event,
        # but the finally block must still reset the contextvar.
        gen = chief_of_staff.run_chief("trigger error", _RaisingLLM())
        try:
            for _ in gen:
                pass
        except Exception:
            pass
        # Critical assertion: contextvar is empty again after the
        # generator exhausts/raises. Stale value would mean future
        # turns on the same worker leak.
        self.assertEqual(current_turn_id.get(), "")

    def test_concurrent_turns_do_not_cross_publish(self):
        """Two overlapping turns: turn-A has a clean Pricing critique +
        Pricing wiki_edit; turn-B has only a Replenishment wiki_edit
        with no critique of its own. With the per-turn-id gate,
        turn-A's auto-publish must not flip turn-B's draft to
        published.
        """
        from app.agents.chief_of_staff import _wiki_auto_publish_clean_drafts
        from app.spine.events import append_event, current_turn_id
        from app.spine.artifacts import write_artifact
        from datetime import datetime, timezone

        turn_start_a = datetime.now(timezone.utc).isoformat()
        turn_a_id = "turn-a-uuid"
        turn_b_id = "turn-b-uuid"

        # Turn A — Pricing draft + clean critique + Pricing wiki_edit
        token_a = current_turn_id.set(turn_a_id)
        try:
            pricing_draft_id = write_artifact(
                agent="Pricing & Promo", kind="plan", title="A draft",
                body_md="b", refs=[], stage="draft",
            )
            critique_id = write_artifact(
                agent="Critic", kind="critique", title="C",
                body_md=(
                    "## Verified\nok\n## Gaps\n*No material findings.*\n"
                    "## Risks\n*No material findings.*\n## Counter-recommendation\nhold\n"
                ),
                refs=[pricing_draft_id], stage="critique",
            )
            append_event(
                agent="Critic", kind="observation",
                payload={"artifact_title": "C"}, artifact_id=critique_id,
            )
            wiki.propose_edit(
                "policy/turn-a", "P", "BODY", "Pricing & Promo",
                refs=[pricing_draft_id],
            )
        finally:
            current_turn_id.reset(token_a)

        # Turn B (overlapping) — only a Replenishment wiki_edit
        token_b = current_turn_id.set(turn_b_id)
        try:
            wiki.propose_edit("policy/turn-b", "P2", "BODY2", "Replenishment")
        finally:
            current_turn_id.reset(token_b)

        # Auto-publish for turn A only
        _wiki_auto_publish_clean_drafts(turn_start_a, llm=None, turn_id=turn_a_id)

        # Turn A's draft published; turn B's stays a draft.
        self.assertEqual(wiki.get_page("policy/turn-a").status, "published")
        self.assertEqual(wiki.get_page("policy/turn-b").status, "draft")

    def test_unreviewed_author_draft_held_even_when_other_critique_clean(self):
        """An Analyst (or any agent whose draft wasn't critiqued this
        turn) authoring a wiki_edit must NOT get auto-published just
        because Pricing's draft happened to get a clean critique.
        Per-draft gate must match wiki_edit.agent against the set of
        agents whose drafts were actually reviewed this turn.
        """
        from app.agents.chief_of_staff import _wiki_auto_publish_clean_drafts
        from app.spine.events import append_event
        from datetime import datetime, timezone

        turn_start = datetime.now(timezone.utc).isoformat()
        # Pricing's draft lands + gets a clean critique
        pricing_draft_id = write_artifact(
            agent="Pricing & Promo", kind="plan", title="Pricing draft",
            body_md="markdown 25%", refs=[], stage="draft",
        )
        clean_id = write_artifact(
            agent="Critic", kind="critique", title="C",
            body_md=(
                "## Verified\nok\n## Gaps\n*No material findings.*\n"
                "## Risks\n*No material findings.*\n## Counter-recommendation\nhold\n"
            ),
            refs=[pricing_draft_id], stage="critique",
        )
        append_event(
            agent="Critic", kind="observation",
            payload={"artifact_title": "C", "stage": "critique"},
            artifact_id=clean_id,
        )
        # Analyst (un-reviewed) and Pricing both author wiki edits
        # Analyst's wiki_edit cites no reviewed artifact → must hold.
        wiki.propose_edit("policy/x", "P", "B", "Analyst")
        # Pricing's wiki_edit refs its reviewed draft → publishes.
        wiki.propose_edit(
            "policy/y", "P2", "B2", "Pricing & Promo",
            refs=[pricing_draft_id],
        )

        _wiki_auto_publish_clean_drafts(turn_start, llm=None)
        # Pricing's wiki edit publishes (its draft was reviewed clean).
        self.assertEqual(wiki.get_page("policy/y").status, "published")
        # Analyst's wiki edit stays a draft (no Analyst draft was
        # critiqued this turn).
        self.assertEqual(wiki.get_page("policy/x").status, "draft")

    def test_curator_drafts_only_auto_publish_via_first_pass(self):
        """Auto-publish must run exactly ONCE per turn — before the
        Curator fires. Curator drafts authored after the Critic phase
        have no critique of their own; a second publish pass would
        promote them on the agent draft's clean review, which is
        unsafe. Verified by inspecting chief_of_staff.run_chief
        source — only one _wiki_auto_publish_clean_drafts call site
        exists (pre-curator); the post-curator pass was removed."""
        import inspect
        from app.agents import chief_of_staff

        src = inspect.getsource(chief_of_staff.run_chief)
        # Exactly one call to the helper inside run_chief.
        self.assertEqual(
            src.count("_wiki_auto_publish_clean_drafts("), 1,
            msg="run_chief must call the wiki auto-publish helper exactly once",
        )

    def test_search_status_filter_applied_before_limit(self):
        """A broad query whose first `limit` rows are all published
        must still surface draft matches when status='draft'.
        Filtering AFTER the LIMIT would silently hide them."""
        # Seed 3 published + 1 draft, all matching the search needle.
        for i in range(3):
            wiki.propose_edit(f"p/foo{i}", "FOO published", "FOO body", "A")
            wiki.publish_page(f"p/foo{i}", "Operator")
        wiki.propose_edit("d/foo3", "FOO draft", "FOO body draft", "A")

        # Limit=3 — published rows would otherwise consume all slots.
        rs_draft = wiki.search_pages("FOO", limit=3, status="draft")
        slugs = {p.slug for p in rs_draft}
        self.assertEqual(slugs, {"d/foo3"})

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
