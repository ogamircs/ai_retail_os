#!/usr/bin/env python
"""DSPy compile entrypoint (Track 7 D3).

Run BootstrapFewShot over an agent's Signature + training set, render
the compiled module into `prompts/<slug>/v<n+1>.md`, and bump
`aliases.json["staging"]` to point at it. The Track 4 M4 prompt
registry then resolves the new version on the next operator turn that
sets `<AGENT>_PROMPT_ALIAS=staging`. The A/B gate (D4) flips `prod`
only after the eval harness rules the new version a winner.

Usage:
    python scripts/dspy_optimize.py analyst
    python scripts/dspy_optimize.py analyst --auto-promote
    python scripts/dspy_optimize.py analyst --max-demos 6 --metric structural

This script is the only place DSPy actually runs. The cockpit demo
path is unaffected when `dspy-ai` isn't installed — the `[dspy]`
extra is opt-in by design.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
PROMPTS_ROOT = REPO_ROOT / "prompts"
BACKEND_ROOT = REPO_ROOT / "backend"

# Make `app.*` importable without a `pip install -e` step — same trick
# the eval harness uses.
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# Registered agents. Each entry maps a slug to its (handwritten policy
# block path, signature factory, dataset loader). Adding a new agent
# is one row — the script's wiring stays generic.
def _agent_registry() -> dict[str, dict]:
    from app.agents.dspy_signatures import analyst as analyst_sig
    from app.agents.dspy_signatures import pricing as pricing_sig
    from app.agents.dspy_dataset import (
        load_analyst_dataset,
        load_jsonl,
        to_examples,
    )

    def _load_pricing_dataset():
        rows = load_jsonl(PROMPTS_ROOT / "training" / "pricing_promo.jsonl")
        return to_examples(rows, input_keys=("operator_question", "category_snapshot"))

    return {
        "analyst": {
            "policy_path": PROMPTS_ROOT / "analyst" / "v1.md",
            "make_module": analyst_sig.make_module,
            "load_dataset": lambda: load_analyst_dataset(PROMPTS_ROOT),
            "input_keys": ("operator_question", "spine_snapshot"),
        },
        "pricing_promo": {
            "policy_path": PROMPTS_ROOT / "pricing_promo" / "v1.md",
            "make_module": pricing_sig.make_module,
            "load_dataset": _load_pricing_dataset,
            "input_keys": ("operator_question", "category_snapshot"),
        },
    }


def _configure_dspy_lm() -> Any:
    """Pick an LM for DSPy based on the same env the cockpit's LLM
    provider reads. Falls back through Anthropic → OpenAI → Google so
    a single API key in .env is enough to compile.
    """
    import dspy  # type: ignore

    if os.getenv("ANTHROPIC_API_KEY"):
        model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        # `dspy.LM` accepts the LiteLLM-style "<provider>/<model>" string.
        lm = dspy.LM(
            f"anthropic/{model}",
            api_key=os.environ["ANTHROPIC_API_KEY"],
            max_tokens=1024,
        )
    elif os.getenv("OPENAI_API_KEY"):
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        lm = dspy.LM(
            f"openai/{model}",
            api_key=os.environ["OPENAI_API_KEY"],
            max_tokens=1024,
        )
    elif os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        model = os.getenv("GOOGLE_MODEL", "gemini-1.5-flash")
        lm = dspy.LM(
            f"gemini/{model}",
            api_key=os.environ.get("GOOGLE_API_KEY") or os.environ["GEMINI_API_KEY"],
            max_tokens=1024,
        )
    else:
        raise RuntimeError(
            "no LLM API key configured — set ANTHROPIC_API_KEY / "
            "OPENAI_API_KEY / GOOGLE_API_KEY in .env before running "
            "the DSPy optimizer."
        )
    dspy.settings.configure(lm=lm)
    return lm


def _structural_metric():
    """Cheap programmatic metric used as the BootstrapFewShot validator.

    Real LLM-as-judge scoring would gate the optimizer on a 4-dim
    rubric — but each judge call costs an LM round-trip and the
    bootstrap pass scores N candidates × M demos. Keep the optimizer
    cheap; defer judge scoring to the A/B gate (D4).

    Passes when:
      - findings_md / evidence_md are non-empty
      - findings_md is bullet-shaped (>=2 lines starting with `- `)
      - evidence_md mentions a numeric or pipe-table marker (cheap
        proxy for "cited evidence")
    """

    def _metric(example: Any, pred: Any, trace: Any = None) -> bool:  # noqa: ARG001
        findings = (getattr(pred, "findings_md", "") or "").strip()
        evidence = (getattr(pred, "evidence_md", "") or "").strip()
        if not findings or not evidence:
            return False
        bullet_lines = [ln for ln in findings.splitlines() if ln.strip().startswith("- ")]
        if len(bullet_lines) < 2:
            return False
        if "|" not in evidence and not any(c.isdigit() for c in evidence):
            return False
        return True

    return _metric


def run_ab_gate(
    agent_slug: str,
    baseline_alias: str = "prod",
    candidate_alias: str = "staging",
) -> dict:
    """A/B run of the eval harness for `agent_slug`. Track 7 D4 gate.

    Runs the eval harness twice — once with `<AGENT>_PROMPT_ALIAS` set
    to `baseline_alias`, once with `candidate_alias`. Pulls the
    `JudgeScore` per scenario from each run and feeds them into
    `compare_aliases` for the verdict.

    The eval harness is expensive — each pass is 4 scenarios × 2 modes
    = 8 LLM-driven turns. The gate runs `multi_pass` only (Track 2 A5
    is fixed at multi_pass for the operator-facing path), halving the
    cost.
    """
    from tests.agents.eval.judge import JudgeScore, compare_aliases  # type: ignore
    from tests.agents.eval.run_eval import run as run_eval  # type: ignore

    def _scores_for(alias: str) -> dict[str, JudgeScore]:
        out = run_eval(
            scenarios=None,
            modes=["multi_pass"],
            prompts_alias={agent_slug: alias},
        )
        scores: dict[str, JudgeScore] = {}
        for scen, modes in (out.get("runs") or {}).items():
            payload = (modes or {}).get("multi_pass") or {}
            sc = payload.get("score") or {}
            scores[scen] = JudgeScore(
                factual_correctness=int(sc.get("factual_correctness", 0)),
                evidence_cited=int(sc.get("evidence_cited", 0)),
                policy_adherence=int(sc.get("policy_adherence", 0)),
                recommendation_quality=int(sc.get("recommendation_quality", 0)),
                notes=str(sc.get("notes", "")),
            )
        return scores

    a_scores = _scores_for(baseline_alias)
    b_scores = _scores_for(candidate_alias)
    return compare_aliases(a_scores, b_scores)


def _try_mlflow():
    """Lazy mlflow loader — same shape as the eval harness. Returns
    None when MLflow isn't configured so the optimizer still works in
    a stripped environment."""
    if not os.getenv("MLFLOW_TRACKING_URI"):
        return None
    try:
        import mlflow  # type: ignore

        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT", "dspy-compile"))
        return mlflow
    except Exception:
        return None


def compile_agent(
    agent_slug: str,
    max_demos: int = 4,
    auto_promote: bool = False,
    optimizer: str = "bootstrap",
) -> dict:
    """Compile + render. Returns a small summary dict for logging /
    cockpit display.

    `optimizer` ∈ {"bootstrap", "mipro"}:
      * "bootstrap" (default) — `BootstrapFewShot`. Cheap; selects
        few-shot demos. Ships compiled as v<n+1>.md.
      * "mipro" — `MIPROv2`. Bigger search, optimizer-rewritten
        instructions + demos. Capped at `MIPRO_MAX_BOOTSTRAPPED_DEMOS`
        (default 8) to keep compilation under ~$5/agent. Use for the
        action specialists (Pricing / Marketing / Replenishment) where
        the eval-harness scores plateau on bootstrap alone.
    """
    registry = _agent_registry()
    if agent_slug not in registry:
        raise ValueError(
            f"unknown agent '{agent_slug}' — registered: "
            f"{', '.join(sorted(registry))}"
        )
    spec = registry[agent_slug]

    import dspy  # type: ignore
    from dspy.teleprompt import BootstrapFewShot  # type: ignore

    from app.agents.dspy_compile import (
        bump_alias,
        write_compiled_prompt,
    )

    lm = _configure_dspy_lm()
    module = spec["make_module"]()
    trainset = spec["load_dataset"]()
    if not trainset:
        raise RuntimeError(
            f"training set is empty for '{agent_slug}'. Seed "
            f"`prompts/training/{agent_slug}.jsonl` before compiling."
        )

    metric = _structural_metric()
    if optimizer == "mipro":
        # MIPROv2 — bigger search, instruction rewriting + demo
        # selection. Capped via `MIPRO_MAX_BOOTSTRAPPED_DEMOS` so
        # compilation cost stays bounded (default 8). The cap is read
        # from env so operators can tune without touching code.
        try:
            from dspy.teleprompt import MIPROv2  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "MIPROv2 is not available in this dspy-ai install. "
                "Upgrade with `pip install -U dspy-ai`."
            ) from exc
        cap = int(os.getenv("MIPRO_MAX_BOOTSTRAPPED_DEMOS", "8"))
        teleprompter = MIPROv2(
            metric=metric,
            max_bootstrapped_demos=min(max_demos, cap),
            max_labeled_demos=min(max_demos, cap),
            num_candidates=int(os.getenv("MIPRO_NUM_CANDIDATES", "5")),
        )
    elif optimizer == "bootstrap":
        teleprompter = BootstrapFewShot(
            metric=metric,
            max_bootstrapped_demos=max_demos,
            max_labeled_demos=max_demos,
        )
    else:
        raise ValueError(
            f"unknown optimizer '{optimizer}' — expected 'bootstrap' or 'mipro'"
        )
    mlflow = _try_mlflow()
    t0 = time.monotonic()
    if mlflow is not None:
        run_ctx = mlflow.start_run(run_name=f"dspy_optimize::{agent_slug}")
    else:
        run_ctx = None
    try:
        compiled = teleprompter.compile(module, trainset=trainset)
    except Exception as exc:
        if run_ctx is not None:
            mlflow.set_tag("status", "failed")  # type: ignore
            mlflow.log_text(repr(exc), "error.txt")  # type: ignore
            mlflow.end_run()  # type: ignore
        raise
    elapsed_s = round(time.monotonic() - t0, 2)

    handwritten = spec["policy_path"].read_text() if spec["policy_path"].exists() else ""
    path, version = write_compiled_prompt(
        agent_slug=agent_slug,
        compiled_module=compiled,
        handwritten_policy_block=handwritten,
        prompts_root=PROMPTS_ROOT,
    )
    aliases = bump_alias(agent_slug, "staging", version, PROMPTS_ROOT)
    gate_verdict: dict | None = None
    promoted = False
    if auto_promote:
        # D4 gate: run the eval harness twice (prod alias vs staging),
        # compare with `compare_aliases`, only flip prod when the
        # candidate clears the 3-of-4-scenarios bar AND doesn't
        # regress `policy_adherence` (hard floor).
        gate_verdict = run_ab_gate(agent_slug, baseline_alias="prod", candidate_alias="staging")
        if gate_verdict.get("b_wins"):
            aliases = bump_alias(agent_slug, "prod", version, PROMPTS_ROOT)
            promoted = True

    summary = {
        "agent": agent_slug,
        "version": version,
        "path": str(path.relative_to(REPO_ROOT)),
        "aliases": aliases,
        "elapsed_s": elapsed_s,
        "model": getattr(lm, "model", None),
        "trainset_size": len(trainset),
        "optimizer": optimizer,
        "auto_promote_requested": auto_promote,
        "promoted": promoted,
        "gate_verdict": gate_verdict,
    }

    if run_ctx is not None:
        try:
            mlflow.set_tag("agent", agent_slug)  # type: ignore
            mlflow.set_tag("status", "ok")  # type: ignore
            mlflow.log_metric("trainset_size", float(len(trainset)))  # type: ignore
            mlflow.log_metric("elapsed_s", float(elapsed_s))  # type: ignore
            mlflow.log_dict(aliases, "aliases.json")  # type: ignore
            mlflow.log_text(path.read_text(), f"compiled/{version}.md")  # type: ignore
        finally:
            mlflow.end_run()  # type: ignore

    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("agent", help="Agent slug (e.g. 'analyst', 'pricing_promo')")
    p.add_argument(
        "--max-demos",
        type=int,
        default=4,
        help="max_bootstrapped_demos cap (default 4)",
    )
    p.add_argument(
        "--auto-promote",
        action="store_true",
        help="Flip prod alias when the eval-harness A/B gate passes. "
        "By default only staging is bumped — D4's eval gate is the "
        "promotion path.",
    )
    p.add_argument(
        "--optimizer",
        choices=("bootstrap", "mipro"),
        default="bootstrap",
        help="DSPy teleprompter (default bootstrap). Use 'mipro' for "
        "action specialists where multi-step optimization (instructions "
        "+ demos + tool calls) is worth the bigger search budget.",
    )
    args = p.parse_args()

    try:
        summary = compile_agent(
            agent_slug=args.agent,
            max_demos=args.max_demos,
            auto_promote=args.auto_promote,
            optimizer=args.optimizer,
        )
    except Exception as exc:
        print(f"[dspy_optimize] FAILED: {exc}", file=sys.stderr)
        return 2
    import json

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
