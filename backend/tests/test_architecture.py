"""Architecture fitness — pin the dependency direction.

The architecture doc says deps flow:

    surfaces (frontend) → routes → agents → spine / substrate / integrations

Lower-level layers must not import from higher-level ones. This test
greps source files for forbidden imports and fails fast when one slips
in. Cheap; runs in milliseconds; catches a class of regressions that
PR review tends to wave through.

Why grep instead of an AST walk: every layer is tiny by design, the
import statements are stable formatting, and grep keeps the test
self-contained (no extra dependencies). If false positives ever bite,
swap to `ast.parse`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "backend" / "app"


def _python_files(*roots: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _violations(files: list[Path], forbidden_prefixes: tuple[str, ...]) -> list[tuple[Path, str]]:
    """Return (file, offending line) pairs for any import matching a
    forbidden prefix. Skips comments + docstrings via simple regex."""
    out: list[tuple[Path, str]] = []
    pattern = re.compile(
        r"^\s*(?:from|import)\s+(" + "|".join(re.escape(p) for p in forbidden_prefixes) + r")\b"
    )
    for f in files:
        for line in f.read_text().splitlines():
            if pattern.match(line):
                out.append((f, line.strip()))
    return out


class ArchitectureFitnessTest(unittest.TestCase):
    def test_substrate_does_not_import_agents_or_routes(self) -> None:
        # Substrate is the lowest layer — agents read from it, never
        # the other way round.
        files = _python_files(APP_ROOT / "substrate")
        bad = _violations(
            files,
            ("app.agents", "app.routes", "app.llm.mcp"),
        )
        self.assertEqual(bad, [], msg=f"substrate reaching upward: {bad}")

    def test_spine_does_not_import_agents_or_routes(self) -> None:
        files = _python_files(APP_ROOT / "spine")
        bad = _violations(
            files,
            ("app.agents", "app.routes"),
        )
        self.assertEqual(bad, [], msg=f"spine reaching upward: {bad}")

    def test_integrations_does_not_import_agents_or_routes(self) -> None:
        files = _python_files(APP_ROOT / "integrations")
        bad = _violations(
            files,
            ("app.agents", "app.routes"),
        )
        self.assertEqual(bad, [], msg=f"integrations reaching upward: {bad}")

    def test_agents_do_not_import_routes(self) -> None:
        # Agents are middle-tier; they're allowed to depend on spine /
        # substrate / integrations / llm but must not reach into the
        # FastAPI surface.
        files = _python_files(APP_ROOT / "agents")
        bad = _violations(files, ("app.routes",))
        self.assertEqual(bad, [], msg=f"agents reaching into routes: {bad}")

    def test_llm_does_not_import_agents_or_routes(self) -> None:
        # LLM provider modules sit alongside spine/substrate as
        # building blocks. The mcp client is an exception — the
        # GBrain wiki-backed mock falls back to spine.wiki, which is
        # fine; reaching agents/routes would be backwards.
        files = _python_files(APP_ROOT / "llm")
        bad = _violations(files, ("app.agents", "app.routes"))
        self.assertEqual(bad, [], msg=f"llm reaching upward: {bad}")


if __name__ == "__main__":
    unittest.main()
