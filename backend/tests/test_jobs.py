"""`app.spine.jobs` round-trips. Verifies durability + concurrency cap
helper without touching FastAPI."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from app.spine import db, jobs


class JobsHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            db, "DB_PATH", os.path.join(self.tmp.name, "spine.db")
        )
        self._patch.start()
        db.init_db()

    def tearDown(self) -> None:
        self._patch.stop()
        self.tmp.cleanup()

    def test_create_then_read(self) -> None:
        row = jobs.create_job(
            "abc123",
            kind="dspy_compile",
            title="DSPy compile · analyst",
            metadata={"agent": "analyst", "auto_promote": False},
        )
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["metadata"]["agent"], "analyst")
        self.assertIsNone(row["ended_at"])

        again = jobs.get_job("abc123")
        self.assertIsNotNone(again)
        self.assertEqual(again["id"], "abc123")

    def test_complete_marks_ok(self) -> None:
        jobs.create_job("j1", kind="dspy_compile", title="x")
        jobs.complete_job("j1", summary={"version": "v9"})
        row = jobs.get_job("j1")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["summary"], {"version": "v9"})
        self.assertIsNotNone(row["ended_at"])
        self.assertIsNone(row["error"])

    def test_complete_with_error_marks_error(self) -> None:
        jobs.create_job("j2", kind="dspy_compile", title="x")
        jobs.complete_job("j2", error="boom")
        row = jobs.get_job("j2")
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error"], "boom")

    def test_cancel_only_running(self) -> None:
        jobs.create_job("j3", kind="dspy_compile", title="x")
        jobs.cancel_job("j3", reason="user clicked cancel")
        row = jobs.get_job("j3")
        self.assertEqual(row["status"], "cancelled")
        # Cancelling an already-terminal job is a no-op (status stays).
        jobs.cancel_job("j3", reason="ignored")
        again = jobs.get_job("j3")
        self.assertEqual(again["status"], "cancelled")

    def test_list_filters_by_kind(self) -> None:
        jobs.create_job("a", kind="dspy_compile", title="x")
        jobs.create_job("b", kind="brain_reindex", title="y")
        compile_jobs = jobs.list_jobs(kind="dspy_compile")
        self.assertEqual({j["id"] for j in compile_jobs}, {"a"})

    def test_running_count_per_kind(self) -> None:
        jobs.create_job("r1", kind="dspy_compile", title="x")
        jobs.create_job("r2", kind="dspy_compile", title="x")
        jobs.complete_job("r1", summary={})
        self.assertEqual(jobs.running_count("dspy_compile"), 1)
        self.assertEqual(jobs.running_count("nonexistent"), 0)

    def test_list_orders_newest_first(self) -> None:
        jobs.create_job("first", kind="dspy_compile", title="x")
        jobs.create_job("second", kind="dspy_compile", title="x")
        rows = jobs.list_jobs(kind="dspy_compile")
        self.assertEqual([r["id"] for r in rows[:2]], ["second", "first"])


if __name__ == "__main__":
    unittest.main()
