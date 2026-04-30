"""Tests for Critic guardrails — fail-fast on missing artifact_id and on
critiques without a backlink ref. Both are codex-review P2 fixes from the
A1 PR (#14): runtime tool calls aren't schema-validated, so the impls must
reject malformed input rather than producing a low-value critique or an
orphaned artifact.
"""

import tempfile
import unittest
from pathlib import Path

from app.agents import critic
from app.agents.chief_of_staff import build_orchestrator
from app.spine import db
from app.spine.artifacts import write_artifact
from app.substrate import seed


class _NullLLM:
    """Stub LLMProvider — never used; we only invoke tool impls directly."""

    def chat(self, *_a, **_kw):
        raise AssertionError("LLM should not be called in these tests")


class CriticGuardrailsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_write_artifact_rejects_missing_refs(self):
        """A critique without refs would orphan it — reject before persisting."""
        out = critic._tool_write_artifact(
            {"title": "Critique of X", "body_md": "## Verified\n*No findings.*"}
        )
        self.assertIn("error", out)
        self.assertNotIn("artifact_id", out)

    def test_write_artifact_rejects_empty_refs_list(self):
        out = critic._tool_write_artifact(
            {"title": "Critique of X", "body_md": "## Verified\nok", "refs": []}
        )
        self.assertIn("error", out)

    def test_write_artifact_rejects_blank_refs(self):
        out = critic._tool_write_artifact(
            {"title": "Critique of X", "body_md": "## Verified\nok", "refs": ["", "   "]}
        )
        self.assertIn("error", out)

    def test_write_artifact_persists_when_refs_present(self):
        draft_id = write_artifact(
            agent="Pricing",
            kind="proposal",
            title="Markdown plan",
            body_md="## Summary\nMarkdown SKU-001 by 25%.",
            refs=[],
        )
        out = critic._tool_write_artifact(
            {
                "title": "Critique of Markdown plan",
                "body_md": "## Verified\nok\n## Gaps\nnone\n## Risks\nnone\n## Counter-recommendation\nhold",
                "refs": [draft_id],
            }
        )
        self.assertIn("artifact_id", out)
        self.assertIn("event_id", out)

    def test_delegate_to_critic_rejects_missing_artifact_id(self):
        """Other delegate handlers fail fast on missing required keys.
        delegate_to_critic must do the same — runtime calls aren't schema-
        validated so an empty id would silently launch a useless audit.
        """
        orchestrator = build_orchestrator(_NullLLM(), event_sink=_StubSink())
        impl = orchestrator.tool_impls["delegate_to_critic"]

        out_missing = impl({"task": "audit it"})
        self.assertIn("error", out_missing)

        out_empty = impl({"artifact_id": "", "task": "audit it"})
        self.assertIn("error", out_empty)

        out_blank = impl({"artifact_id": "   ", "task": "audit it"})
        self.assertIn("error", out_blank)


class _StubSink:
    """Matches chief_of_staff._EventBuffer's add() / drain() surface."""

    def __init__(self):
        self.events: list = []

    def add(self, ev):
        self.events.append(ev)

    def drain(self):
        out = self.events[:]
        self.events.clear()
        return out


if __name__ == "__main__":
    unittest.main()
