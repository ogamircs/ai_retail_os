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

from app.agents.chief_of_staff import run_chief
from app.llm import get_provider
from app.spine.artifacts import read_artifact

from tests.agents.eval.judge import JudgeScore, compare_modes, score_transcript
from tests.agents.eval.scenarios import SCENARIOS, by_name


HERE = Path(__file__).resolve().parent
LAST_RUN_PATH = HERE / "last_run.json"


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


def run(scenarios: list[str] | None = None, modes: list[str] | None = None) -> dict:
    scenarios = scenarios or [s.name for s in SCENARIOS]
    modes = modes or ["single_pass", "multi_pass"]
    runs: dict[str, dict[str, dict]] = {}
    for s in scenarios:
        runs[s] = {}
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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scenario", nargs="?", help="Limit to one scenario (default: all)")
    p.add_argument("--mode", choices=["single_pass", "multi_pass"], help="Limit to one mode")
    args = p.parse_args()

    scenarios = [args.scenario] if args.scenario else None
    modes = [args.mode] if args.mode else None

    out = run(scenarios=scenarios, modes=modes)
    print(json.dumps(out["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
