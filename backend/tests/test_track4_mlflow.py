"""Track 4 — MLflow + prompt registry + telemetry unit tests.

These tests do NOT require an actual MLflow server. The tracing
wrapper degrades to a no-op when MLFLOW_TRACE_ENABLED is unset OR
when `import mlflow` fails. We verify both branches without
installing mlflow as a test dep.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from app.llm import tracing as mesh_tracing
from app.llm.base import AssistantTurn, Message, Tool
from app.llm.prompts import (
    active_version,
    list_versions,
    resolve_prompt,
    _PROMPTS_ROOT,
)
from app.spine import db, telemetry
from app.spine.events import append_event
from app.substrate import seed


class _StubProvider:
    name = "stub"
    model = "stub-1"

    def __init__(self):
        self.calls: list[tuple[str, list, list]] = []

    def chat(self, system, messages, tools):
        self.calls.append((system, messages, tools))
        return AssistantTurn(text="stub response", tool_calls=[], raw_blocks=[], stop_reason="end")


class TracingNoopTest(unittest.TestCase):
    """When MLFLOW_TRACE_ENABLED is unset, every helper is a no-op and
    the wrapper is the identity provider."""

    def setUp(self):
        os.environ.pop("MLFLOW_TRACE_ENABLED", None)
        # Reset module state so the lazy mlflow loader retries on the
        # next invocation (the cached `_mlflow_load_attempted=True`
        # would otherwise stick across tests).
        mesh_tracing._mlflow_module = None
        mesh_tracing._mlflow_load_attempted = False

    def test_is_tracing_enabled_false_by_default(self):
        self.assertFalse(mesh_tracing.is_tracing_enabled())

    def test_wrap_provider_returns_inner_when_disabled(self):
        inner = _StubProvider()
        wrapped = mesh_tracing.wrap_provider(inner)
        self.assertIs(wrapped, inner)

    def test_turn_run_is_noop_when_disabled(self):
        # Should not raise; should not open any run.
        with mesh_tracing.turn_run("hi", "stub", "stub-1"):
            with mesh_tracing.delegate_run("Pricing & Promo"):
                pass

    def test_log_event_is_noop_when_disabled(self):
        # No exception, no side effect.
        mesh_tracing.log_event("test", {"x": 1})

    def test_log_artifact_link_is_noop_when_disabled(self):
        mesh_tracing.log_artifact_link("aid", "report", "draft")


class TracingProviderEnabledNoMlflowTest(unittest.TestCase):
    """When MLFLOW_TRACE_ENABLED is set but mlflow is not importable,
    the provider wrapper still works — chat() flows through; logging
    is silently dropped."""

    def setUp(self):
        os.environ["MLFLOW_TRACE_ENABLED"] = "1"
        mesh_tracing._mlflow_module = None
        mesh_tracing._mlflow_load_attempted = False

    def tearDown(self):
        os.environ.pop("MLFLOW_TRACE_ENABLED", None)
        mesh_tracing._mlflow_module = None
        mesh_tracing._mlflow_load_attempted = False

    def test_is_tracing_enabled_true_when_env_set(self):
        self.assertTrue(mesh_tracing.is_tracing_enabled())

    def test_wrap_provider_returns_decorator(self):
        inner = _StubProvider()
        wrapped = mesh_tracing.wrap_provider(inner)
        # The decorator presents the same chat() shape; identity is the
        # important thing — that's the contract callers depend on.
        self.assertIsNot(wrapped, inner)
        self.assertEqual(wrapped.name, "stub")
        self.assertEqual(wrapped.model, "stub-1")

    def test_chat_flows_through_when_mlflow_unavailable(self):
        """If `import mlflow` fails (the realistic CI path with no
        optional deps installed), chat() must not raise — it should
        simply skip the trace and forward to the inner provider."""
        inner = _StubProvider()
        wrapped = mesh_tracing.wrap_provider(inner)
        # Force-poison the loader so the import path returns None even
        # if mlflow happens to be installed in the test environment.
        original_loader = mesh_tracing._try_import_mlflow
        try:
            mesh_tracing._try_import_mlflow = lambda: None  # type: ignore
            out = wrapped.chat(
                "system",
                [Message(role="user", content="hello")],
                [],
            )
            self.assertEqual(out.text, "stub response")
            self.assertEqual(len(inner.calls), 1)
        finally:
            mesh_tracing._try_import_mlflow = original_loader  # type: ignore

    def test_loader_does_not_clobber_caller_set_experiment(self):
        """The lazy mlflow loader must not override an experiment the
        caller (e.g. run_eval.py) already set. Otherwise the first
        traced chat() during eval runs would reset the experiment back
        to the cockpit default and eval runs would land in the wrong
        namespace.
        """
        # Stub the mlflow module surface — we only need set_tracking_uri
        # and set_experiment to assert they were/weren't called.
        os.environ.pop("MLFLOW_TRACKING_URI", None)
        os.environ.pop("MLFLOW_EXPERIMENT_NAME", None)

        called: dict[str, list] = {"uri": [], "exp": []}

        class _StubMlflow:
            @staticmethod
            def set_tracking_uri(u):
                called["uri"].append(u)

            @staticmethod
            def set_experiment(e):
                called["exp"].append(e)

        # Inject the stub by patching the import surface. Easiest way
        # without a real mlflow install: monkey-patch sys.modules.
        import sys as _sys

        old_mod = _sys.modules.get("mlflow")
        _sys.modules["mlflow"] = _StubMlflow  # type: ignore
        try:
            mesh_tracing._mlflow_module = None
            mesh_tracing._mlflow_load_attempted = False
            mod = mesh_tracing._try_import_mlflow()
            self.assertIs(mod, _StubMlflow)
            # No env vars set → loader must not touch globals.
            self.assertEqual(called["uri"], [])
            self.assertEqual(called["exp"], [])

            # With MLFLOW_TRACKING_URI set, only the URI should be set.
            mesh_tracing._mlflow_module = None
            mesh_tracing._mlflow_load_attempted = False
            os.environ["MLFLOW_TRACKING_URI"] = "http://localhost:5500"
            mesh_tracing._try_import_mlflow()
            self.assertEqual(called["uri"], ["http://localhost:5500"])
            self.assertEqual(called["exp"], [])
        finally:
            if old_mod is not None:
                _sys.modules["mlflow"] = old_mod
            else:
                _sys.modules.pop("mlflow", None)
            os.environ.pop("MLFLOW_TRACKING_URI", None)


class PromptRegistryTest(unittest.TestCase):
    """Track 4 M4 — alias resolution chain. Uses a tempdir-scoped
    prompts root so the test doesn't rely on whatever `prompts/` is
    checked into the repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_root = mesh_tracing  # placeholder
        # Patch the module-level _PROMPTS_ROOT pointer.
        from app.llm import prompts as _p

        self._old_root = _p._PROMPTS_ROOT
        _p._PROMPTS_ROOT = Path(self.tmp.name)

    def tearDown(self):
        from app.llm import prompts as _p

        _p._PROMPTS_ROOT = self._old_root
        self.tmp.cleanup()
        # Drop any per-agent env vars set during the test
        for k in (
            "TESTAGENT_PROMPT_ALIAS",
            "TESTAGENT_PROMPT_OVERRIDE",
        ):
            os.environ.pop(k, None)

    def _seed(self, agent_slug: str, files: dict[str, str], aliases: dict[str, str]):
        d = Path(self.tmp.name) / agent_slug
        d.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            (d / f"{name}.md").write_text(body)
        (d / "aliases.json").write_text(json.dumps(aliases))

    def test_falls_back_to_in_code_system_when_registry_empty(self):
        out = resolve_prompt("Analyst", "FALLBACK")
        self.assertEqual(out, "FALLBACK")
        self.assertIsNone(active_version("Analyst"))

    def test_resolves_prod_alias_by_default(self):
        self._seed("testagent", {"v1": "PROD-V1", "v2": "STG-V2"}, {"prod": "v1", "staging": "v2"})
        self.assertEqual(resolve_prompt("testagent", "FALLBACK"), "PROD-V1")
        self.assertEqual(active_version("testagent"), "v1")

    def test_alias_env_var_flips_resolution(self):
        self._seed("testagent", {"v1": "PROD-V1", "v2": "STG-V2"}, {"prod": "v1", "staging": "v2"})
        os.environ["TESTAGENT_PROMPT_ALIAS"] = "staging"
        self.assertEqual(resolve_prompt("testagent", "FALLBACK"), "STG-V2")
        self.assertEqual(active_version("testagent"), "v2")

    def test_override_env_var_takes_priority(self):
        self._seed("testagent", {"v1": "PROD-V1"}, {"prod": "v1"})
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("OVERRIDE-CONTENT")
            override_path = f.name
        try:
            os.environ["TESTAGENT_PROMPT_OVERRIDE"] = override_path
            self.assertEqual(resolve_prompt("testagent", "FALLBACK"), "OVERRIDE-CONTENT")
            self.assertEqual(active_version("testagent"), "override")
        finally:
            Path(override_path).unlink(missing_ok=True)

    def test_unresolved_alias_falls_back(self):
        """Alias points at a missing version → return fallback; don't
        crash + don't silently load v1."""
        self._seed("testagent", {"v1": "V1"}, {"prod": "v99"})
        self.assertEqual(resolve_prompt("testagent", "FALLBACK"), "FALLBACK")

    def test_list_versions_returns_sorted_filenames(self):
        self._seed("testagent", {"v3": "x", "v1": "y", "v10": "z"}, {})
        # Lex sort, not numeric — operator-readable; if they want
        # numeric they can trust the v<n> convention.
        self.assertEqual(list_versions("testagent"), ["v1", "v10", "v3"])

    def test_agent_name_with_punctuation_slugs_correctly(self):
        self._seed("pricing_promo", {"v1": "PRICING"}, {"prod": "v1"})
        self.assertEqual(resolve_prompt("Pricing & Promo", "FALLBACK"), "PRICING")


class TelemetryAggregatorTest(unittest.TestCase):
    """Track 4 M5 — telemetry rolls up the spine event log into a
    `telemetry` artifact."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_aggregate_counts_events_by_agent(self):
        append_event(agent="Pricing & Promo", kind="proposal", payload={"x": 1})
        append_event(agent="Pricing & Promo", kind="action", payload={"x": 1})
        append_event(agent="Marketing", kind="proposal", payload={"x": 1})

        data = telemetry.aggregate(window_hours=24)
        self.assertGreaterEqual(data["agents"]["Pricing & Promo"]["proposal"], 1)
        self.assertGreaterEqual(data["agents"]["Pricing & Promo"]["action"], 1)
        self.assertGreaterEqual(data["agents"]["Marketing"]["proposal"], 1)

    def test_aggregate_counts_mesh_downgrades(self):
        append_event(agent="Chief of Staff", kind="mesh_downgrade", payload={"reason": "test"})
        append_event(agent="Chief of Staff", kind="mesh_downgrade", payload={"reason": "test"})
        data = telemetry.aggregate(window_hours=24)
        self.assertGreaterEqual(data["downgrade_count"], 2)

    def test_run_aggregation_writes_telemetry_artifact(self):
        from app.spine.artifacts import read_artifact

        aid = telemetry.run_aggregation(window_hours=24)
        art = read_artifact(aid)
        self.assertIsNotNone(art)
        self.assertEqual(art["kind"], "telemetry")
        self.assertEqual(art["stage"], "final")
        self.assertEqual(art["agent"], "Telemetry")
        self.assertIn("Telemetry — last 24h", art["title"])

    def test_aggregate_separates_sync_success_failure(self):
        append_event(
            agent="Integration",
            kind="measurement",
            payload={"action": "sync_inbound", "system_id": "akeneo", "status": "success"},
        )
        append_event(
            agent="Integration",
            kind="measurement",
            payload={"action": "sync_inbound", "system_id": "akeneo", "status": "error", "error": "boom"},
        )
        data = telemetry.aggregate(window_hours=24)
        self.assertGreaterEqual(data["syncs"]["akeneo"].get("success", 0), 1)
        self.assertGreaterEqual(data["syncs"]["akeneo"].get("failure", 0), 1)


class PromptRegistryRealRepoTest(unittest.TestCase):
    """Sanity-check that the repo-shipped prompts/analyst/v1.md is
    valid + the alias resolution doesn't accidentally break in CI."""

    def test_analyst_has_at_least_one_version(self):
        # Reach the real prompts dir (not the test tempdir).
        from app.llm import prompts as _p

        # _PROMPTS_ROOT may have been monkey-patched in earlier tests;
        # restore by re-importing the module's default value via the
        # filesystem path.
        original = _p._PROMPTS_ROOT
        _p._PROMPTS_ROOT = Path(__file__).resolve().parent.parent.parent / "prompts"
        try:
            versions = list_versions("Analyst")
            self.assertGreaterEqual(len(versions), 1, msg=f"got {versions!r}")
            prompt = resolve_prompt("Analyst", "FALLBACK")
            # Either fallback (registry empty / repo not seeded) or one
            # of the seeded versions. Both are acceptable; the test
            # just asserts the call doesn't raise.
            self.assertTrue(isinstance(prompt, str) and len(prompt) > 0)
        finally:
            _p._PROMPTS_ROOT = original


if __name__ == "__main__":
    unittest.main()
