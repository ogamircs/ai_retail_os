"""Env-gated wrapper around the Track 2 A5 eval harness.

Skipped when:
  * No LLM API key is configured (no ANTHROPIC_API_KEY / OPENAI_API_KEY /
    GOOGLE_API_KEY / GEMINI_API_KEY in the env).
  * `RUN_EVAL` env var is not set to a truthy value — the harness costs
    real LLM tokens, so it never fires automatically. CI gates on this.

When invoked, runs all 4 scenarios in both modes and asserts the spec
target: multi-pass strictly higher on at least 3 of 4 dimensions on at
least 3 of 4 scenarios.
"""

from __future__ import annotations

import os
import unittest


def _have_llm_key() -> bool:
    return any(
        os.getenv(k, "").strip()
        for k in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
        )
    )


def _eval_enabled() -> bool:
    return os.getenv("RUN_EVAL", "").strip().lower() in ("1", "true", "yes")


SKIP_REASON = None
if not _have_llm_key():
    SKIP_REASON = "no LLM API key in env"
elif not _eval_enabled():
    SKIP_REASON = "RUN_EVAL not set; skipping costly LLM eval"


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class MeshEvalTest(unittest.TestCase):
    def test_multi_pass_beats_single_pass_on_majority_dimensions(self) -> None:
        from tests.agents.eval.run_eval import run

        out = run()
        summary = out["summary"]
        # Spec: multi-pass strictly higher on >=3 of 4 dimensions on >=3 of 4 scenarios.
        self.assertGreaterEqual(
            summary["scenarios_with_strict_win"],
            3,
            msg=(
                "multi-pass mesh did not strictly beat single-pass on >=3 of 4 "
                f"scenarios. Detail:\n{summary}"
            ),
        )

    def test_no_judge_no_tool_call_outliers(self) -> None:
        """The judge should always submit a tool call; if any score has
        notes='judge_no_tool_call' the run is unreliable.
        """
        from tests.agents.eval.run_eval import run

        out = run()
        outliers = []
        for s, modes in out["runs"].items():
            for m, payload in modes.items():
                if payload["score"].get("notes") == "judge_no_tool_call":
                    outliers.append((s, m))
        self.assertEqual(outliers, [], msg=f"judge fell off the tool-call rails on: {outliers}")


if __name__ == "__main__":
    unittest.main()
