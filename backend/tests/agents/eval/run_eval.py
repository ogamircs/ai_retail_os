"""Run the Track 2 A5 eval harness end-to-end against a real LLM.

Usage:
    python -m tests.agents.eval.run_eval                # run all 4 scenarios, both modes
    python -m tests.agents.eval.run_eval overstock_summer
    python -m tests.agents.eval.run_eval --mode single_pass  weekend_heatwave

Writes a JSON summary to backend/tests/agents/eval/last_run.json so the
unittest wrapper (test_eval.py) can assert pass/fail without re-running.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from app.agents.chief_of_staff import run_chief
from app.llm import get_provider
from app.spine.artifacts import read_artifact

from tests.agents.eval.judge import JudgeScore, compare_modes, score_transcript
from tests.agents.eval.scenarios import SCENARIOS, by_name


HERE = Path(__file__).resolve().parent
LAST_RUN_PATH = HERE / "last_run.json"


def _git_sha() -> str:
    """Best-effort current git sha for MLflow experiment naming."""
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=HERE,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        sha = out.stdout.strip()
        return sha or "nogit"
    except Exception:
        return "nogit"


def _try_mlflow():
    """Lazy mlflow loader for the eval harness — same shape as
    tracing.py's loader. Returns None if mlflow isn't installed or the
    tracking server isn't configured."""
    if not os.getenv("MLFLOW_TRACKING_URI"):
        return None
    try:
        import mlflow  # type: ignore

        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        return mlflow
    except Exception:
        return None


def _run_one(scenario_name: str, mode: str) -> tuple[str, str, list[dict]]:
    """Drive one operator turn end-to-end, return (transcript, final, artifacts)."""
    scenario = by_name(scenario_name)
    # Single-pass = mesh disabled. Multi-pass = mesh enabled.
    if mode == "single_pass":
        os.environ["MESH_ENABLED"] = "0"
    else:
        os.environ["MESH_ENABLED"] = "1"

    llm = get_provider()
    transcript_lines: list[str] = []
    artifact_ids: list[str] = []
    final = ""
    for ev in run_chief(scenario.prompt, llm):
        if ev.kind == "text":
            transcript_lines.append(f"[{ev.agent}] {ev.data.get('text','')}")
        elif ev.kind == "tool_call":
            transcript_lines.append(
                f"[{ev.agent}] tool_call {ev.data.get('tool')} {json.dumps(ev.data.get('input',{}))[:200]}"
            )
        elif ev.kind == "tool_result":
            r = ev.data.get("result", {})
            if isinstance(r, dict) and "artifact_id" in r:
                artifact_ids.append(r["artifact_id"])
            transcript_lines.append(
                f"[{ev.agent}] tool_result {ev.data.get('tool')} {str(r)[:200]}"
            )
        elif ev.kind == "agent_end":
            if ev.agent == "Chief of Staff":
                final = ev.data.get("text", "")
        elif ev.kind == "error":
            transcript_lines.append(f"[{ev.agent}] error {ev.data.get('error')}")
    artifacts = [read_artifact(aid) for aid in artifact_ids if read_artifact(aid)]
    return "\n".join(transcript_lines), final, artifacts


def run(
    scenarios: list[str] | None = None,
    modes: list[str] | None = None,
    prompts_alias: dict[str, str] | None = None,
) -> dict:
    """Drive the eval harness.

    `prompts_alias` (Track 7 D4): per-agent alias overrides applied
    via `<AGENT>_PROMPT_ALIAS` env vars for the duration of the run.
    The map is keyed by agent slug (matches the prompt registry, e.g.
    'analyst', 'pricing_promo'). When None or empty, the default prod
    alias resolves — same behaviour as before D4.
    """
    scenarios = scenarios or [s.name for s in SCENARIOS]
    modes = modes or ["single_pass", "multi_pass"]
    if prompts_alias:
        # `<AGENT>_PROMPT_ALIAS` is what `app.llm.prompts.resolve_prompt`
        # already reads; setting the env at run() entry point means
        # every nested chief.run() call resolves the requested version
        # without us threading the alias through the agent builders.
        for slug, alias in prompts_alias.items():
            env_name = f"{slug.upper()}_PROMPT_ALIAS"
            os.environ[env_name] = alias
    runs: dict[str, dict[str, dict]] = {}
    mlflow = _try_mlflow()
    sha = _git_sha()
    for s in scenarios:
        runs[s] = {}
        # Track 4 M3: each scenario maps to one MLflow experiment;
        # single-pass + multi-pass land as nested runs underneath so
        # the comparison view shows them side-by-side.
        if mlflow is not None:
            mlflow.set_experiment(f"eval/{s}/{sha}")
        for m in modes:
            t0 = time.monotonic()
            transcript, final, artifacts = _run_one(s, m)
            dt = round(time.monotonic() - t0, 2)
            score = score_transcript(by_name(s), transcript, final, artifacts)
            runs[s][m] = {
                "score": score.to_dict(),
                "elapsed_s": dt,
                "artifact_count": len(artifacts),
                "final_chars": len(final),
            }
            if mlflow is not None:
                _log_eval_run(mlflow, s, m, sha, score, dt, transcript, final, artifacts)
    # Aggregate (need raw JudgeScore objects)
    raw: dict[str, dict[str, JudgeScore]] = {}
    for s, modes_d in runs.items():
        raw[s] = {}
        for m, payload in modes_d.items():
            sc = payload["score"]
            raw[s][m] = JudgeScore(
                factual_correctness=sc["factual_correctness"],
                evidence_cited=sc["evidence_cited"],
                policy_adherence=sc["policy_adherence"],
                recommendation_quality=sc["recommendation_quality"],
                notes=sc.get("notes", ""),
            )
    summary = compare_modes(raw)
    out = {"runs": runs, "summary": summary, "scenarios": scenarios, "modes": modes}
    LAST_RUN_PATH.write_text(json.dumps(out, indent=2))
    return out


def _log_eval_run(
    mlflow: Any,
    scenario: str,
    mode: str,
    sha: str,
    score: JudgeScore,
    elapsed_s: float,
    transcript: str,
    final: str,
    artifacts: list[dict],
) -> None:
    """Push one (scenario × mode) run into MLflow.

    Each run carries:
      * tags: scenario, mode, git_sha
      * metrics: factual_correctness, evidence_cited, policy_adherence,
        recommendation_quality, latency_s, total_score, artifact_count
      * artifacts: full transcript, judge notes, final reply, generated
        artifact bodies
    """
    try:
        with mlflow.start_run(run_name=f"{mode}::{scenario}"):
            mlflow.set_tags({"scenario": scenario, "mode": mode, "git_sha": sha})
            mlflow.log_metric("factual_correctness", float(score.factual_correctness))
            mlflow.log_metric("evidence_cited", float(score.evidence_cited))
            mlflow.log_metric("policy_adherence", float(score.policy_adherence))
            mlflow.log_metric("recommendation_quality", float(score.recommendation_quality))
            mlflow.log_metric("total_score", float(score.total()))
            mlflow.log_metric("latency_s", float(elapsed_s))
            mlflow.log_metric("artifact_count", float(len(artifacts)))
            mlflow.log_text(transcript, "transcript.txt")
            mlflow.log_text(final, "final_reply.txt")
            mlflow.log_dict(score.to_dict(), "judge_score.json")
            for a in artifacts:
                # Stable filename per artifact id so re-runs overwrite
                # in place (cheaper than new artifact paths each time).
                aid = (a or {}).get("id") or "anon"
                mlflow.log_dict(a, f"artifacts/{aid}.json")
    except Exception:
        # MLflow logging must never abort the eval — score still lands
        # in last_run.json.
        pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scenario", nargs="?", help="Limit to one scenario (default: all)")
    p.add_argument("--mode", choices=["single_pass", "multi_pass"], help="Limit to one mode")
    p.add_argument(
        "--prompts-alias",
        action="append",
        default=[],
        help="Per-agent prompt alias override, repeatable. Format: "
        "'<agent_slug>=<alias>' (e.g. 'analyst=staging'). Sets the "
        "`<AGENT>_PROMPT_ALIAS` env for the run so the prompt registry "
        "resolves the requested version. Track 7 D4 — A/B test "
        "compiled DSPy prompts against the current prod alias.",
    )
    args = p.parse_args()

    scenarios = [args.scenario] if args.scenario else None
    modes = [args.mode] if args.mode else None
    alias_map: dict[str, str] = {}
    for entry in args.prompts_alias or []:
        if "=" not in entry:
            print(f"[run_eval] ignoring malformed --prompts-alias '{entry}' — expected slug=alias")
            continue
        slug, alias = entry.split("=", 1)
        slug = slug.strip()
        alias = alias.strip()
        if slug and alias:
            alias_map[slug] = alias

    out = run(scenarios=scenarios, modes=modes, prompts_alias=alias_map or None)
    print(json.dumps(out["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
