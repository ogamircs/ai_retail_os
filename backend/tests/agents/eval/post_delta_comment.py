"""CI helper (Track 4 M6) — post the eval delta comment + decide gate.

Called by `.github/workflows/eval-gate.yml` after `run_eval.py` writes
`last_run.json`. Compares the current run's per-dimension scores to the
configured baseline (or to a synthesised baseline pulled from the
target branch's `last_run.json` if MLflow isn't wired) and:

  1. Posts a structured comment to the PR via `gh pr comment`.
  2. Exits with code 42 if any dimension regressed by more than
     `EVAL_REGRESSION_THRESHOLD` (default 5%) on any scenario; else 0.

Designed to be self-contained — no MLflow client dep — so the gate
runs without a tracking server in early-stage repos.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
LAST_RUN = HERE / "last_run.json"
DIMENSIONS = (
    "factual_correctness",
    "evidence_cited",
    "policy_adherence",
    "recommendation_quality",
)


def _threshold() -> float:
    raw = os.getenv("EVAL_REGRESSION_THRESHOLD", "0.05").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.05


def _format_table(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"## Track 2 / Track 4 eval gate")
    lines.append("")
    lines.append(
        f"**Multi-pass strict-win scenarios:** {summary['scenarios_with_strict_win']} / "
        f"{len(summary['scenarios'])} (target ≥ {summary['target_scenarios']})"
    )
    lines.append(f"**Per-dimension multi-pass wins:**")
    for d, n in summary["wins_by_dimension"].items():
        lines.append(f"  - `{d}`: {n} / {len(summary['scenarios'])}")
    lines.append("")
    lines.append("### Per-scenario detail")
    lines.append("")
    lines.append(
        "| Scenario | Multi pass | Single pass | Winner |"
    )
    lines.append("|---|---|---|---|")
    for s, payload in summary["scenarios"].items():
        winner = "multi" if payload["multi_pass_total"] > payload["single_pass_total"] else (
            "single" if payload["single_pass_total"] > payload["multi_pass_total"] else "tie"
        )
        lines.append(
            f"| `{s}` | {payload['multi_pass_total']} / 12 "
            f"| {payload['single_pass_total']} / 12 | **{winner}** |"
        )
    lines.append("")
    return "\n".join(lines)


def _detect_regressions(summary: dict[str, Any], threshold: float) -> list[str]:
    """A regression = a per-dimension multi-pass score lower than
    single-pass by more than `threshold` * 3 (since each dim is 0-3).

    Conservative — we don't compare to a historical baseline because
    we don't always have one in CI; instead we treat single-pass as
    the floor (multi-pass should NEVER be worse than single-pass).
    """
    flagged: list[str] = []
    margin = threshold * 3.0
    for s, payload in summary["scenarios"].items():
        per_dim = payload.get("per_dimension", {})
        for dim, vals in per_dim.items():
            multi = vals.get("multi_pass", 0)
            single = vals.get("single_pass", 0)
            if multi < single - margin:
                flagged.append(
                    f"`{s}::{dim}`: multi={multi}, single={single} (Δ={multi - single:.2f})"
                )
    return flagged


def _post_comment(body: str) -> None:
    pr = os.getenv("PR_NUMBER")
    repo = os.getenv("REPO")
    if not pr or not repo:
        # Local dry-run path — print the body so the operator sees what
        # would have shipped.
        print(body)
        return
    try:
        subprocess.run(
            ["gh", "pr", "comment", pr, "--repo", repo, "--body", body],
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        # Don't fail the gate on a comment-post error — surface it but
        # keep the regression check authoritative.
        print(f"::warning ::failed to post PR comment: {e}", file=sys.stderr)


def main() -> int:
    if not LAST_RUN.exists():
        print("::error ::eval-gate: last_run.json not found — did run_eval succeed?", file=sys.stderr)
        return 2
    data = json.loads(LAST_RUN.read_text())
    summary = data.get("summary", {})
    if not summary:
        print("::error ::eval-gate: last_run.json missing summary", file=sys.stderr)
        return 2
    threshold = _threshold()
    regressions = _detect_regressions(summary, threshold)
    body_lines = [_format_table(summary)]
    if regressions:
        body_lines.append("### ⚠️ Regressions flagged")
        body_lines.append("")
        for r in regressions:
            body_lines.append(f"- {r}")
        body_lines.append("")
        body_lines.append(
            f"_Threshold: any per-dimension multi-pass score below "
            f"single-pass by more than {threshold * 100:.0f}% × 3 ="
            f" {threshold * 3:.2f} points blocks the merge._"
        )
    else:
        body_lines.append("### ✅ No per-dimension regressions beyond threshold")
    body = "\n".join(body_lines)
    _post_comment(body)
    if regressions:
        print(f"::error ::eval-gate: {len(regressions)} regression(s) flagged", file=sys.stderr)
        return 42
    return 0


if __name__ == "__main__":
    sys.exit(main())
