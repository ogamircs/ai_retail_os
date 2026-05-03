"""Track 7 — DSPy + prompt optimization unit tests.

Covers the parts of the rollout that don't require the optional
`dspy-ai` extra to be installed:

  * `dspy_compile.compiled_to_markdown` — turns a fake compiled module
    (just an object with a `.demos` list) into the markdown shape the
    prompt registry resolves.
  * `dspy_compile.write_compiled_prompt` — picks the next v<n+1>.md.
  * `dspy_compile.bump_alias` — preserves other aliases.
  * `dspy_dataset.load_jsonl` + `_split_markdown_sections` — pure
    Python helpers, work without dspy-ai.
  * `judge.compare_aliases` — A/B gate verdict shape.
  * `/api/dspy/agents` route — read-only over `aliases.json`.
  * `/api/dspy/optimize/{slug}` — kicks off a job; we patch the worker
    so tests don't need dspy-ai.

The full optimizer round-trip (`scripts/dspy_optimize.py compile_agent`)
is env-gated under `RUN_DSPY=1` because it needs `dspy-ai` installed
*and* a real LLM API key — too heavy for CI.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CompiledToMarkdownTest(unittest.TestCase):
    """`compiled_to_markdown` must preserve the handwritten policy
    block verbatim AND append a `## Few-shot demos` section the prompt
    registry can read."""

    def test_no_demos_emits_empty_marker(self):
        from app.agents.dspy_compile import compiled_to_markdown

        class _M:
            demos = []

        body = compiled_to_markdown(_M(), "Handwritten policy.\n")
        self.assertIn("Handwritten policy.", body)
        self.assertIn("## Few-shot demos", body)
        self.assertIn("No demos selected", body)

    def test_demo_keys_render_as_field_lines(self):
        from app.agents.dspy_compile import compiled_to_markdown

        class _M:
            demos = [
                {"operator_question": "q?", "findings_md": "- one\n- two"},
                {"operator_question": "q2?", "findings_md": "- three\n- four"},
            ]

        body = compiled_to_markdown(_M(), "Policy.")
        self.assertIn("### Demo 1", body)
        self.assertIn("**operator_question:** q?", body)
        # Multi-line demo values must be fenced so the prompt file
        # stays parseable.
        self.assertIn("**findings_md:**", body)
        self.assertIn("```", body)
        self.assertIn("### Demo 2", body)

    def test_handwritten_block_preserved_at_top(self):
        from app.agents.dspy_compile import compiled_to_markdown

        class _M:
            demos = []

        body = compiled_to_markdown(_M(), "First line.\nSecond line.")
        # Handwritten content lands BEFORE the demos heading.
        idx_handwritten = body.index("First line.")
        idx_heading = body.index("## Few-shot demos")
        self.assertLess(idx_handwritten, idx_heading)


class WriteCompiledPromptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_compile_lands_at_v1(self):
        from app.agents.dspy_compile import write_compiled_prompt

        class _M:
            demos = []

        path, version = write_compiled_prompt(
            agent_slug="canary",
            compiled_module=_M(),
            handwritten_policy_block="Policy.",
            prompts_root=self.root,
        )
        self.assertEqual(version, "v1")
        self.assertTrue(path.name == "v1.md")
        self.assertTrue(path.exists())

    def test_subsequent_compile_picks_next_version(self):
        from app.agents.dspy_compile import write_compiled_prompt

        (self.root / "canary").mkdir(parents=True)
        (self.root / "canary" / "v1.md").write_text("v1")
        (self.root / "canary" / "v2.md").write_text("v2")

        class _M:
            demos = []

        _, version = write_compiled_prompt(
            agent_slug="canary",
            compiled_module=_M(),
            handwritten_policy_block="Policy.",
            prompts_root=self.root,
        )
        self.assertEqual(version, "v3")

    def test_concurrent_compiles_pick_distinct_versions(self):
        """Two threads racing on the same agent dir must end up with
        DIFFERENT version files — the atomic O_CREAT|O_EXCL guard
        means the loser of the v(n+1) slot rescans and bumps to
        v(n+2), instead of silently overwriting the winner."""
        import threading

        from app.agents.dspy_compile import write_compiled_prompt

        class _M:
            demos = []

        results: list[str] = []
        errors: list[Exception] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(4)

        def _run():
            barrier.wait()
            try:
                _, version = write_compiled_prompt(
                    agent_slug="canary",
                    compiled_module=_M(),
                    handwritten_policy_block="Policy.",
                    prompts_root=self.root,
                )
                with results_lock:
                    results.append(version)
            except Exception as e:
                with results_lock:
                    errors.append(e)

        threads = [threading.Thread(target=_run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"unexpected errors: {errors}")
        self.assertEqual(len(results), 4)
        # All distinct versions — none collided. Since no prior files
        # existed, the four compiles must produce v1..v4 in some order.
        self.assertEqual(sorted(results), ["v1", "v2", "v3", "v4"])
        # Every version file actually landed on disk with content.
        for v in results:
            p = self.root / "canary" / f"{v}.md"
            self.assertTrue(p.exists())
            self.assertIn("Policy.", p.read_text())

    def test_bump_alias_preserves_other_aliases(self):
        from app.agents.dspy_compile import bump_alias

        (self.root / "canary").mkdir(parents=True)
        (self.root / "canary" / "aliases.json").write_text(
            json.dumps({"prod": "v1", "experimental": "v2"})
        )
        new = bump_alias("canary", "staging", "v3", self.root)
        self.assertEqual(new["prod"], "v1")
        self.assertEqual(new["experimental"], "v2")
        self.assertEqual(new["staging"], "v3")
        # File should round-trip.
        on_disk = json.loads((self.root / "canary" / "aliases.json").read_text())
        self.assertEqual(on_disk, new)


class DspyDatasetTest(unittest.TestCase):
    def test_load_jsonl_skips_blank_and_bad_lines(self):
        from app.agents.dspy_dataset import load_jsonl

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write('{"a": 1}\n\n')
            f.write("not json\n")
            f.write('{"b": 2}\n')
            path = Path(f.name)
        try:
            rows = load_jsonl(path)
        finally:
            path.unlink()
        self.assertEqual(rows, [{"a": 1}, {"b": 2}])

    def test_load_jsonl_returns_empty_for_missing_file(self):
        from app.agents.dspy_dataset import load_jsonl

        rows = load_jsonl(Path("/nonexistent/training.jsonl"))
        self.assertEqual(rows, [])

    def test_split_markdown_sections_keys_lowercased(self):
        from app.agents.dspy_dataset import _split_markdown_sections

        body = "# Findings\n- a\n- b\n## Evidence\n| x | y |\n## Open Questions\n- q?"
        sec = _split_markdown_sections(body)
        self.assertIn("findings", sec)
        self.assertIn("evidence", sec)
        self.assertIn("open questions", sec)
        self.assertIn("- a", sec["findings"])

    def test_committed_analyst_jsonl_is_valid(self):
        """The repo ships a seed training set at
        prompts/training/analyst.jsonl. Every row must parse and carry
        the four expected fields, otherwise the optimizer's first run
        will silently degrade."""
        from app.agents.dspy_dataset import load_jsonl

        repo_root = Path(__file__).resolve().parent.parent.parent
        path = repo_root / "prompts" / "training" / "analyst.jsonl"
        if not path.exists():
            self.skipTest("seed training set not present")
        rows = load_jsonl(path)
        self.assertGreaterEqual(len(rows), 1)
        for row in rows:
            for key in ("operator_question", "spine_snapshot", "findings_md", "evidence_md"):
                self.assertIn(key, row, f"row missing {key!r}: {row}")


class RunEvalAliasEnvRestoreTest(unittest.TestCase):
    """Track 7 D4 P1 fix: `<AGENT>_PROMPT_ALIAS` env vars set by
    `run()` must be restored on exit (and on exception). Otherwise
    `run_ab_gate` leaks the staging override into subsequent /api/chat
    turns and breaks the prod gate semantics."""

    def setUp(self):
        # Snapshot + clear the env vars we care about so tests start clean.
        self._saved = {}
        for k in ("ANALYST_PROMPT_ALIAS", "PRICING_PROMPT_ALIAS"):
            self._saved[k] = os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_alias_env_restored_after_successful_run(self):
        from tests.agents.eval import run_eval

        # Patch the inner run-one to a no-op that doesn't need an LLM.
        from tests.agents.eval.judge import JudgeScore

        with mock.patch.object(
            run_eval, "_run_one", return_value=("transcript", "final", [])
        ), mock.patch.object(
            run_eval,
            "score_transcript",
            return_value=JudgeScore(2, 2, 2, 2, ""),
        ):
            run_eval.run(
                scenarios=["overstock_summer"],
                modes=["multi_pass"],
                prompts_alias={"analyst": "staging"},
            )
        # The alias must be unset on exit since it wasn't set before.
        self.assertNotIn("ANALYST_PROMPT_ALIAS", os.environ)

    def test_alias_env_restored_to_prior_value(self):
        from tests.agents.eval import run_eval
        from tests.agents.eval.judge import JudgeScore

        os.environ["ANALYST_PROMPT_ALIAS"] = "experimental"
        with mock.patch.object(
            run_eval, "_run_one", return_value=("t", "f", [])
        ), mock.patch.object(
            run_eval,
            "score_transcript",
            return_value=JudgeScore(2, 2, 2, 2, ""),
        ):
            run_eval.run(
                scenarios=["overstock_summer"],
                modes=["multi_pass"],
                prompts_alias={"analyst": "staging"},
            )
        # Prior value restored exactly — not 'staging', not unset.
        self.assertEqual(os.environ["ANALYST_PROMPT_ALIAS"], "experimental")

    def test_alias_env_restored_on_exception(self):
        from tests.agents.eval import run_eval

        def _boom(*a, **kw):
            raise RuntimeError("inner failure")

        with mock.patch.object(run_eval, "_run_one", side_effect=_boom):
            with self.assertRaises(RuntimeError):
                run_eval.run(
                    scenarios=["overstock_summer"],
                    modes=["multi_pass"],
                    prompts_alias={"analyst": "staging"},
                )
        # Even though run() raised, the env must be cleaned up.
        self.assertNotIn("ANALYST_PROMPT_ALIAS", os.environ)


class CompareAliasesTest(unittest.TestCase):
    def _scores(self, **kw):
        from tests.agents.eval.judge import JudgeScore

        defaults = dict(
            factual_correctness=2,
            evidence_cited=2,
            policy_adherence=2,
            recommendation_quality=2,
            notes="",
        )
        defaults.update(kw)
        return JudgeScore(**defaults)

    def test_b_wins_when_three_of_four_dimensions_strictly_higher(self):
        from tests.agents.eval.judge import compare_aliases

        a = {
            "s1": self._scores(),
            "s2": self._scores(),
            "s3": self._scores(),
            "s4": self._scores(),
        }
        # b strictly wins on 3/4 dims for 3 scenarios, ties on s4.
        b = {
            "s1": self._scores(factual_correctness=3, evidence_cited=3, recommendation_quality=3),
            "s2": self._scores(factual_correctness=3, evidence_cited=3, recommendation_quality=3),
            "s3": self._scores(factual_correctness=3, evidence_cited=3, recommendation_quality=3),
            "s4": self._scores(),
        }
        verdict = compare_aliases(a, b)
        self.assertTrue(verdict["b_wins"])
        self.assertEqual(verdict["scenarios_b_strict_win"], 3)
        self.assertTrue(verdict["policy_floor_ok"])

    def test_b_loses_when_policy_regresses_on_any_scenario(self):
        from tests.agents.eval.judge import compare_aliases

        a = {f"s{i}": self._scores() for i in range(1, 5)}
        # b sweeps every scenario but drops policy on s2 — must fail.
        b = {
            "s1": self._scores(factual_correctness=3, evidence_cited=3, recommendation_quality=3),
            "s2": self._scores(
                factual_correctness=3,
                evidence_cited=3,
                recommendation_quality=3,
                policy_adherence=1,  # regression
            ),
            "s3": self._scores(factual_correctness=3, evidence_cited=3, recommendation_quality=3),
            "s4": self._scores(factual_correctness=3, evidence_cited=3, recommendation_quality=3),
        }
        verdict = compare_aliases(a, b)
        self.assertFalse(verdict["policy_floor_ok"])
        self.assertFalse(verdict["b_wins"])

    def test_b_loses_when_only_two_of_four_scenarios_clear_dim_bar(self):
        from tests.agents.eval.judge import compare_aliases

        a = {f"s{i}": self._scores() for i in range(1, 5)}
        b = {
            "s1": self._scores(factual_correctness=3, evidence_cited=3, recommendation_quality=3),
            "s2": self._scores(factual_correctness=3, evidence_cited=3, recommendation_quality=3),
            "s3": self._scores(),
            "s4": self._scores(),
        }
        verdict = compare_aliases(a, b)
        self.assertFalse(verdict["b_wins"])


class DspyApiRoutesTest(unittest.TestCase):
    """Cockpit surface for D6: list agents, kick off compile, poll job."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)

    def test_list_agents_returns_aliases(self):
        r = self.client.get("/api/dspy/agents")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        agents = body.get("agents") or []
        self.assertGreaterEqual(len(agents), 1)
        analyst = next(a for a in agents if a["slug"] == "analyst")
        # Either the seed alias or None — but the keys must be present.
        self.assertIn("prod", analyst)
        self.assertIn("staging", analyst)

    def test_compile_route_kicks_off_background_job(self):
        # Patch the worker so the test doesn't need dspy-ai.
        from app import main as main_mod

        called: dict = {}

        def _fake_worker(job_id: str, agent_slug: str, auto_promote: bool):
            called["job_id"] = job_id
            with main_mod._DSPY_JOBS_LOCK:
                main_mod._DSPY_JOBS[job_id].update(
                    {"status": "ok", "summary": {"version": "v9"}}
                )

        with mock.patch.object(main_mod, "_run_dspy_compile_job", _fake_worker):
            r = self.client.post("/api/dspy/optimize/analyst")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("job_id", body)
        self.assertEqual(body["status"], "running")
        # Worker patched to land terminal state synchronously.
        r2 = self.client.get(f"/api/dspy/jobs/{body['job_id']}")
        self.assertEqual(r2.status_code, 200)
        job = r2.json()
        self.assertEqual(job["status"], "ok")
        self.assertEqual(job["summary"]["version"], "v9")

    def test_compile_route_rejects_unknown_agent(self):
        r = self.client.post("/api/dspy/optimize/nonexistent_agent")
        self.assertEqual(r.status_code, 404)

    def test_jobs_list_returns_recent_first(self):
        r = self.client.get("/api/dspy/jobs?limit=5")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("jobs", body)


@unittest.skipUnless(
    os.getenv("RUN_DSPY"),
    "set RUN_DSPY=1 + ANTHROPIC_API_KEY (or OPENAI_API_KEY) to run live DSPy compile",
)
class DspyCompileLiveTest(unittest.TestCase):
    """Env-gated end-to-end. Imports `dspy`, runs a 1-demo compile,
    checks the rendered prompt file shape."""

    def test_compile_analyst_emits_versioned_markdown(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
        try:
            import dspy_optimize  # type: ignore
        except ImportError:
            self.skipTest("scripts/dspy_optimize not importable")
        # Use a low max-demos to keep the cost minimal.
        summary = dspy_optimize.compile_agent(
            agent_slug="analyst", max_demos=1, auto_promote=False
        )
        self.assertEqual(summary["agent"], "analyst")
        self.assertTrue(summary["version"].startswith("v"))
        self.assertEqual(summary["aliases"].get("staging"), summary["version"])


if __name__ == "__main__":
    unittest.main()
