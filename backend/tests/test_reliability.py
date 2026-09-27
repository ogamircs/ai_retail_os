"""Reliability guards: SQLite concurrency settings, the improvement-audit
concurrency cap, and LLM retry wiring across all three providers."""

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app.spine import db, events


class SpineConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_connections_use_wal_and_busy_timeout(self):
        with db.conn() as c:
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(
                c.execute("PRAGMA busy_timeout").fetchone()[0], db.BUSY_TIMEOUT_MS
            )

    def test_open_read_transaction_does_not_block_writer(self):
        # The case WAL actually fixes: under the default rollback journal a
        # reader mid-transaction (e.g. a long cockpit poll) holds a SHARED
        # lock, so a writer can't commit and fails after busy_timeout. Under
        # WAL the reader keeps its snapshot and the writer commits.
        reader = db.sqlite3.connect(db.DB_PATH)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM events").fetchone()
            with mock.patch.object(db, "BUSY_TIMEOUT_MS", 200):
                events.append_event("writer", "observation", {"during": "read"})
        finally:
            reader.rollback()
            reader.close()
        with db.conn() as c:
            n = c.execute("SELECT COUNT(*) FROM events WHERE agent = 'writer'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_concurrent_writers_all_land(self):
        # Smoke test: many short writers racing. Passes under either journal
        # mode thanks to busy_timeout; guards against lost/erroring writes.
        writers, per_writer = 8, 25
        errors: list[BaseException] = []
        start = threading.Barrier(writers)

        def _write(n: int) -> None:
            try:
                start.wait()
                for i in range(per_writer):
                    events.append_event(f"writer-{n}", "observation", {"i": i})
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_write, args=(n,)) for n in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        with db.conn() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM events WHERE agent LIKE 'writer-%'"
            ).fetchone()[0]
        self.assertEqual(n, writers * per_writer)


class ImprovementAuditCapTest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        db.init_db()
        self.client = TestClient(app)

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def _wait_terminal(self, run_id: str) -> dict:
        run: dict = {}
        for _ in range(60):
            run = self.client.get(f"/api/improvements/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        return run

    def test_second_run_rejected_while_first_is_active(self):
        from app.agents import improvement_auditor as ia

        release = threading.Event()

        def _blocking_audit(run_id, llm):
            release.wait(timeout=5)
            ia.complete_run(run_id, {"suggestions": 0})

        with (
            mock.patch("app.routes.improvements.get_provider", return_value=object()),
            mock.patch.object(ia, "run_audit", _blocking_audit),
            mock.patch.dict(os.environ, {"IMPROVEMENT_AUDIT_MAX_CONCURRENT": "1"}),
        ):
            first = self.client.post("/api/improvements/run")
            self.assertEqual(first.status_code, 200)

            second = self.client.post("/api/improvements/run")
            self.assertEqual(second.status_code, 429)
            self.assertIn("already running", second.json()["detail"])

            release.set()
            self.assertEqual(self._wait_terminal(first.json()["run_id"])["status"], "ok")

            # Slot is released once the worker finishes.
            third = None
            for _ in range(20):
                third = self.client.post("/api/improvements/run")
                if third.status_code == 200:
                    break
                time.sleep(0.05)
            assert third is not None
            self.assertEqual(third.status_code, 200)
            self._wait_terminal(third.json()["run_id"])

    def test_worker_crash_still_releases_slot(self):
        from app.agents import improvement_auditor as ia

        def _crashing_audit(run_id, llm):
            raise RuntimeError("boom")

        with (
            mock.patch("app.routes.improvements.get_provider", return_value=object()),
            mock.patch.object(ia, "run_audit", _crashing_audit),
            mock.patch.dict(os.environ, {"IMPROVEMENT_AUDIT_MAX_CONCURRENT": "1"}),
        ):
            first = self.client.post("/api/improvements/run")
            run = self._wait_terminal(first.json()["run_id"])
            self.assertEqual(run["status"], "error")

            again = None
            for _ in range(20):
                again = self.client.post("/api/improvements/run")
                if again.status_code == 200:
                    break
                time.sleep(0.05)
            assert again is not None
            self.assertEqual(again.status_code, 200)
            self._wait_terminal(again.json()["run_id"])


class LlmRetryWiringTest(unittest.TestCase):
    """`LLM_MAX_RETRIES` must reach each SDK's built-in retry mechanism.
    Constructing a client makes no network call, so a dummy key is fine."""

    def _provider(self, name: str, key_env: str, retries: str):
        from app.llm import get_provider

        env = {"LLM_PROVIDER": name, key_env: "test-key", "LLM_MAX_RETRIES": retries}
        with (
            mock.patch.dict(os.environ, env),
            mock.patch("app.llm.wrap_provider", side_effect=lambda p: p),
        ):
            return get_provider()

    def test_anthropic_client_gets_max_retries(self):
        p = self._provider("anthropic", "ANTHROPIC_API_KEY", "5")
        self.assertEqual(p.client.max_retries, 5)  # type: ignore[attr-defined]

    def test_openai_client_gets_max_retries(self):
        p = self._provider("openai", "OPENAI_API_KEY", "5")
        self.assertEqual(p.client.max_retries, 5)  # type: ignore[attr-defined]

    def test_google_client_gets_retry_attempts(self):
        p = self._provider("google", "GOOGLE_API_KEY", "5")
        opts = p.client._api_client._http_options.retry_options  # type: ignore[attr-defined]
        # google-genai counts the original request as an attempt.
        self.assertEqual(opts.attempts, 6)

    def test_invalid_value_falls_back_to_default(self):
        from app.config import settings

        with mock.patch.dict(os.environ, {"LLM_MAX_RETRIES": "lots"}):
            self.assertEqual(settings.llm_max_retries, 4)
        with mock.patch.dict(os.environ, {"LLM_MAX_RETRIES": "-3"}):
            self.assertEqual(settings.llm_max_retries, 0)


if __name__ == "__main__":
    unittest.main()
