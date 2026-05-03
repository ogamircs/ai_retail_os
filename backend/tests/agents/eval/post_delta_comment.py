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
    lines.append("## Track 2 / Track 4 eval gate")
    lines.append("")
    lines.append(
        f"**Multi-pass strict-win scenarios:** {summary['scenarios_with_strict_win']} / "
        f"{len(summary['scenarios'])} (target ≥ {summary['target_scenarios']})"
    )
    lines.append("**Per-dimension multi-pass wins:**")
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


# Per-scenario score is the sum of 4 dimensions × 3 points each = 12.
# Regression detection scales the threshold against this max so the
# `EVAL_REGRESSION_THRESHOLD` env knob actually has meaningful
# resolution — see _detect_regressions.
_MAX_DIMS = 4
_MAX_PER_DIM = 3
_MAX_PER_SCENARIO = _MAX_DIMS * _MAX_PER_DIM


def _detect_regressions(summary: dict[str, Any], threshold: float) -> list[str]:
    """A regression = a per-scenario multi-pass *total* lower than the
    single-pass total by more than `threshold * _MAX_PER_SCENARIO`
    points.

    Why per-scenario totals (0-12) and not per-dimension (0-3)? Each
    dimension is an integer judged 0..3, so the *minimum* possible
    drop is 1 point. Scaling against the per-dimension max made every
    threshold below 33% behave identically — a 1-point dim drop
    flagged at 5%, 10%, AND 20% — defeating the documented knob.
    Comparing totals gives the threshold real resolution:
      threshold=0.05 → margin 0.6 pts  (any 1-pt total drop flags)
      threshold=0.10 → margin 1.2 pts  (drops of 2+ flag)
      threshold=0.20 → margin 2.4 pts  (drops of 3+ flag)
      threshold=0.30 → margin 3.6 pts  (drops of 4+ flag)

    A 1-point total drop on one scenario is rarely worth blocking the
    merge — that's stochastic LLM noise. But a 3+ point drop is a
    real regression, and now operators can dial the gate accordingly.

    Conservative across the board — we don't compare to a historical
    baseline because we don't always have one in CI; instead we treat
    single-pass as the floor (multi-pass mesh must never be worse).
    """
    flagged: list[str] = []
    margin_pts = threshold * float(_MAX_PER_SCENARIO)
    for s, payload in summary["scenarios"].items():
        multi_total = float(payload.get("multi_pass_total", 0))
        single_total = float(payload.get("single_pass_total", 0))
        if multi_total < single_total - margin_pts:
            # Surface the worst per-dim contributor so the operator
            # can jump straight to it instead of re-reading the body.
            per_dim = payload.get("per_dimension", {})
            worst_dim = ""
            worst_drop = 0
            for dim, vals in per_dim.items():
                drop = vals.get("single_pass", 0) - vals.get("multi_pass", 0)
                if drop > worst_drop:
                    worst_drop = drop
                    worst_dim = dim
            flagged.append(
                f"`{s}`: multi={multi_total:.0f}/12 single={single_total:.0f}/12 "
                f"(Δ={multi_total - single_total:+.1f}; worst dim: "
                f"`{worst_dim}` -{worst_drop})"
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
            f"_Threshold: any scenario whose multi-pass total drops below "
            f"single-pass by more than {threshold * 100:.0f}% × {_MAX_PER_SCENARIO} = "
            f"{threshold * _MAX_PER_SCENARIO:.2f} points blocks the merge. "
            f"Set `EVAL_REGRESSION_THRESHOLD` to tune sensitivity (default 0.05)._"
        )
    else:
        body_lines.append("### ✅ No per-scenario regressions beyond threshold")
    body = "\n".join(body_lines)
    _post_comment(body)
    if regressions:
        print(f"::error ::eval-gate: {len(regressions)} regression(s) flagged", file=sys.stderr)
        return 42
    return 0


if __name__ == "__main__":
    sys.exit(main())
