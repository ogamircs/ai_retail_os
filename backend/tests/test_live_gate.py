"""Unit tests for `tests._live_gate`.

The gate is a single decision tree (opt-in → env present → reachable). If
any branch regresses, every live suite either silently fires against drift
or never fires at all — both painful. These tests pin the contract.
"""

from __future__ import annotations

import os
import unittest

from tests import _live_gate


class LiveGateTest(unittest.TestCase):
    def setUp(self) -> None:
        # Snapshot env keys we'll mutate so each test starts clean.
        self._snapshot = {
            k: os.environ.pop(k, None)
            for k in (
                "RUN_LIVE_TESTS",
                "RUN_DEMO_LIVE",
                "DEMO_BASE_URL",
                "DEMO_TOKEN",
            )
        }

    def tearDown(self) -> None:
        for k, v in self._snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_opt_in_false_by_default(self) -> None:
        self.assertFalse(_live_gate.opt_in("demo"))

    def test_opt_in_true_via_master_flag(self) -> None:
        os.environ["RUN_LIVE_TESTS"] = "1"
        self.assertTrue(_live_gate.opt_in("demo"))

    def test_opt_in_true_via_per_system_flag(self) -> None:
        os.environ["RUN_DEMO_LIVE"] = "true"
        self.assertTrue(_live_gate.opt_in("demo"))
        self.assertFalse(_live_gate.opt_in("other"))

    def test_skip_reason_blocks_when_no_opt_in(self) -> None:
        os.environ["DEMO_BASE_URL"] = "https://demo"
        os.environ["DEMO_TOKEN"] = "tok"
        reason = _live_gate.skip_reason(
            "demo", ("DEMO_BASE_URL", "DEMO_TOKEN"), reachable=lambda: True
        )
        self.assertIsNotNone(reason)
        self.assertIn("opt-in only", reason)

    def test_skip_reason_lists_missing_env(self) -> None:
        os.environ["RUN_DEMO_LIVE"] = "1"
        reason = _live_gate.skip_reason("demo", ("DEMO_BASE_URL", "DEMO_TOKEN"))
        self.assertIsNotNone(reason)
        self.assertIn("DEMO_BASE_URL", reason)
        self.assertIn("DEMO_TOKEN", reason)

    def test_skip_reason_unreachable_falls_through(self) -> None:
        os.environ["RUN_DEMO_LIVE"] = "1"
        os.environ["DEMO_BASE_URL"] = "https://demo"
        os.environ["DEMO_TOKEN"] = "tok"
        reason = _live_gate.skip_reason(
            "demo", ("DEMO_BASE_URL", "DEMO_TOKEN"), reachable=lambda: False
        )
        self.assertIsNotNone(reason)
        self.assertIn("not reachable", reason)

    def test_skip_reason_none_when_everything_satisfied(self) -> None:
        os.environ["RUN_DEMO_LIVE"] = "1"
        os.environ["DEMO_BASE_URL"] = "https://demo"
        os.environ["DEMO_TOKEN"] = "tok"
        reason = _live_gate.skip_reason(
            "demo", ("DEMO_BASE_URL", "DEMO_TOKEN"), reachable=lambda: True
        )
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
