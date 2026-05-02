"""LLM-as-judge for the eval harness.

Produces a 0-3 score per dimension on the rubric defined by each
scenario. Uses the same configured LLM provider as the cockpit so the
judge naturally improves alongside the operator-facing model. The eval
harness compares single-pass vs multi-pass scores per scenario; the
judge has no access to which mode produced which transcript (we shuffle
ordering before scoring).

Scoring rubric — exposed to the judge in the system prompt:
  3 = decisive win for this dimension (strong evidence, no caveats)
  2 = solid; minor flaws
  1 = weak; major flaws or hedging
  0 = not addressed or wrong

The judge's output is a single JSON object with a key per dimension and
a one-line `notes` field for human spot-checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.llm import get_provider
from app.llm.base import Message, Tool

from tests.agents.eval.scenarios import EvalScenario


@dataclass(frozen=True)
class JudgeScore:
    factual_correctness: int
    evidence_cited: int
    policy_adherence: int
    recommendation_quality: int
    notes: str = ""

    def total(self) -> int:
        return (
            self.factual_correctness
            + self.evidence_cited
            + self.policy_adherence
            + self.recommendation_quality
        )

    def to_dict(self) -> dict:
        return {
            "factual_correctness": self.factual_correctness,
            "evidence_cited": self.evidence_cited,
            "policy_adherence": self.policy_adherence,
            "recommendation_quality": self.recommendation_quality,
            "notes": self.notes,
            "total": self.total(),
        }


JUDGE_SYSTEM = """You are an impartial judge scoring an AI retail-ops chat reply.

You see:
  * the operator's original prompt
  * the rubric for this scenario (four named dimensions)
  * the agent transcript (Chief of Staff + delegated specialists)
  * the final operator-facing reply
  * the artifacts the run produced (titles + bodies)

For each rubric dimension, score 0..3:
  3 = decisive win (strong evidence, no caveats)
  2 = solid (minor flaws)
  1 = weak (major flaws or hedging)
  0 = not addressed or wrong

Be strict. If a number is cited and it's plausibly fabricated (no spine
query result in the transcript supports it), it's at most 1. If a
recommendation has no alternative considered, recommendation_quality is
at most 2.

Respond with EXACTLY one tool call to `submit_score`. Do not write text.
"""


def _build_judge_tool() -> Tool:
    return Tool(
        name="submit_score",
        description="Submit your 0-3 score per rubric dimension and one-line notes.",
        input_schema={
            "type": "object",
            "properties": {
                "factual_correctness": {"type": "integer", "minimum": 0, "maximum": 3},
                "evidence_cited": {"type": "integer", "minimum": 0, "maximum": 3},
                "policy_adherence": {"type": "integer", "minimum": 0, "maximum": 3},
                "recommendation_quality": {"type": "integer", "minimum": 0, "maximum": 3},
                "notes": {"type": "string"},
            },
            "required": [
                "factual_correctness",
                "evidence_cited",
                "policy_adherence",
                "recommendation_quality",
            ],
        },
    )


def score_transcript(
    scenario: EvalScenario,
    transcript: str,
    final_reply: str,
    artifacts: list[dict],
) -> JudgeScore:
    """Score one transcript. Returns a JudgeScore.

    Falls back to all-zero score with notes='judge_no_tool_call' if the
    judge returns text instead of a tool call (rare). Caller can still
    aggregate; a zero score on every dimension flags the transcript for
    manual review rather than silently inflating downstream means.
    """
    judge_tool = _build_judge_tool()
    artifact_block = "\n\n---\n\n".join(
        f"# Artifact: {a.get('title','(untitled)')}\n"
        f"agent={a.get('agent','?')} kind={a.get('kind','?')} stage={a.get('stage','?')}\n\n"
        f"{a.get('body','')}"
        for a in artifacts
    )
    user_msg = (
        scenario.judge_prompt_context()
        + "\n\n----- Transcript -----\n"
        + transcript
        + "\n\n----- Final operator-facing reply -----\n"
        + final_reply
        + "\n\n----- Artifacts -----\n"
        + (artifact_block or "(none)")
    )
    llm = get_provider()
    turn = llm.chat(JUDGE_SYSTEM, [Message(role="user", content=user_msg)], [judge_tool])
    if not turn.tool_calls:
        return JudgeScore(0, 0, 0, 0, notes="judge_no_tool_call")
    args = turn.tool_calls[0].input or {}
    return JudgeScore(
        factual_correctness=int(args.get("factual_correctness", 0)),
        evidence_cited=int(args.get("evidence_cited", 0)),
        policy_adherence=int(args.get("policy_adherence", 0)),
        recommendation_quality=int(args.get("recommendation_quality", 0)),
        notes=str(args.get("notes", "")),
    )


def compare_modes(per_scenario_scores: dict[str, dict[str, JudgeScore]]) -> dict:
    """Aggregate single-pass vs multi-pass scores across scenarios.

    `per_scenario_scores[scenario_name][mode] = JudgeScore`.
    Returns a structured comparison the test asserts against.
    """
    dimensions = (
        "factual_correctness",
        "evidence_cited",
        "policy_adherence",
        "recommendation_quality",
    )
    out: dict = {"scenarios": {}, "wins_by_dimension": {d: 0 for d in dimensions}, "wins_total": 0}
    scenarios_with_strict_win = 0
    for scenario, modes in per_scenario_scores.items():
        single = modes.get("single_pass")
        multi = modes.get("multi_pass")
        if single is None or multi is None:
            continue
        per_dim_wins = {}
        for d in dimensions:
            multi_v = getattr(multi, d)
            single_v = getattr(single, d)
            won = multi_v > single_v
            per_dim_wins[d] = {
                "multi_pass": multi_v,
                "single_pass": single_v,
                "multi_wins": won,
            }
            if won:
                out["wins_by_dimension"][d] += 1
        out["scenarios"][scenario] = {
            "multi_pass_total": multi.total(),
            "single_pass_total": single.total(),
            "per_dimension": per_dim_wins,
            "multi_pass_notes": multi.notes,
            "single_pass_notes": single.notes,
        }
        # Strict win: multi >= 3 of 4 dimensions strictly higher.
        if sum(1 for d in dimensions if per_dim_wins[d]["multi_wins"]) >= 3:
            scenarios_with_strict_win += 1
            out["wins_total"] += 1
    out["scenarios_with_strict_win"] = scenarios_with_strict_win
    out["target_scenarios"] = 3  # spec: >=3 of 4 scenarios
    out["target_dimensions_per_scenario"] = 3  # spec: >=3 of 4 dimensions
    return out


def serialize_score(s: JudgeScore) -> str:
    return json.dumps(s.to_dict(), indent=2)


def compare_aliases(
    scores_a: dict[str, JudgeScore],
    scores_b: dict[str, JudgeScore],
    target_scenarios: int = 3,
    target_dimensions: int = 3,
) -> dict:
    """A/B compare two prompt aliases scored on the same scenarios
    (Track 7 D4). `scores_a` is the baseline (typically `prod`),
    `scores_b` is the candidate (typically `staging`).

    Win criterion mirrors the Track 2 A5 bar — `b` wins when:
      * `b` strictly beats `a` on >= `target_dimensions` of the 4
        dimensions, on >= `target_scenarios` of the 4 scenarios.
      * AND `b` does not regress `policy_adherence` on ANY scenario
        (hard floor — DSPy can drift on style; can't drift on rules).

    Returns a structured verdict dict with `b_wins`, `policy_floor_ok`,
    per-scenario per-dimension breakdown, and notes for the audit log.
    """
    dimensions = (
        "factual_correctness",
        "evidence_cited",
        "policy_adherence",
        "recommendation_quality",
    )
    scenarios = sorted(set(scores_a) & set(scores_b))
    per_scenario: dict[str, dict] = {}
    scenarios_b_wins = 0
    policy_floor_ok = True
    for s in scenarios:
        a = scores_a[s]
        b = scores_b[s]
        dim_wins: dict[str, dict] = {}
        wins_this_scenario = 0
        for d in dimensions:
            a_v = getattr(a, d)
            b_v = getattr(b, d)
            won = b_v > a_v
            dim_wins[d] = {"a": a_v, "b": b_v, "b_wins": won}
            if won:
                wins_this_scenario += 1
        # Hard policy floor — candidate must not regress policy on any
        # scenario or the gate fails outright (regardless of dimension
        # wins elsewhere).
        if dim_wins["policy_adherence"]["b"] < dim_wins["policy_adherence"]["a"]:
            policy_floor_ok = False
        per_scenario[s] = {
            "a_total": a.total(),
            "b_total": b.total(),
            "per_dimension": dim_wins,
            "b_strict_win": wins_this_scenario >= target_dimensions,
        }
        if wins_this_scenario >= target_dimensions:
            scenarios_b_wins += 1
    b_wins = policy_floor_ok and scenarios_b_wins >= target_scenarios
    return {
        "b_wins": b_wins,
        "policy_floor_ok": policy_floor_ok,
        "scenarios_b_strict_win": scenarios_b_wins,
        "target_scenarios": target_scenarios,
        "target_dimensions_per_scenario": target_dimensions,
        "scenarios": per_scenario,
    }
