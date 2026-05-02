# Track 7 — DSPy + prompt optimization rollout

> Status: **D1–D4 + D6 + D7 landed**. D5 (MIPROv2 + multi-step) deferred — pending operator demand.

## What this lands

- An optional `[dspy]` extra in `backend/pyproject.toml` so the cockpit demo path runs identically without DSPy installed.
- A canary Signature for the **Analyst** agent (`backend/app/agents/dspy_signatures/analyst.py`) — read-only, deterministic-ish, scorable by the existing eval harness.
- A bridge module (`backend/app/agents/dspy_compile.py`) that turns a compiled `dspy.Module` into the same `prompts/<slug>/v<n+1>.md` shape the Track 4 M4 registry already resolves.
- A training-set loader (`backend/app/agents/dspy_dataset.py`) + a seed dataset committed at `prompts/training/analyst.jsonl` (4 hand-curated examples, one per eval scenario).
- An optimizer entrypoint (`scripts/dspy_optimize.py`) that runs `BootstrapFewShot`, renders the compiled module to v<n+1>.md, bumps `aliases.json["staging"]`, and (with `--auto-promote`) runs the eval-harness A/B gate to flip `prod`.
- An eval-harness `--prompts-alias` arg (`backend/tests/agents/eval/run_eval.py`) that scopes a run to a specific alias by setting the `<AGENT>_PROMPT_ALIAS` env for the duration of the call.
- A judge-side `compare_aliases` helper (`backend/tests/agents/eval/judge.py`) that produces the verdict the auto-promote gate consumes.
- A cockpit panel in the `[REPORTS]` tab driving `/api/dspy/optimize/{slug}` and `/api/dspy/jobs/{job_id}`. Operators see a `compile → staging` and a `compile + auto-promote` button per registered agent, plus a live status chip.

## Why BootstrapFewShot first

`BootstrapFewShot` is the lightest DSPy optimizer:

- One LLM call per training example to score against the metric (we use a cheap structural metric — bullets shaped, evidence numeric — to avoid LLM-as-judge during the bootstrap pass).
- No instruction rewriting — the handwritten policy block from v1 stays untouched. The compiled output is purely the policy block plus selected demos.
- ~$0.10 / compile on Sonnet / GPT-4o-mini for 4 demos × 4 examples.

MIPROv2 (D5) is the upgrade path when:
- The Analyst's eval scores plateau on BootstrapFewShot.
- We want optimizer-rewritten instructions, not just demo selection.
- Action specialists (Pricing, Marketing, Replenishment) come into scope — multi-step optimization needs the bigger search.

## Why the gate is asymmetric

The A/B gate in `judge.compare_aliases` does NOT use a symmetric "best total wins" rule. It enforces:

1. **Soft criterion (style/quality):** candidate wins ≥3 of 4 dimensions on ≥3 of 4 scenarios.
2. **Hard floor (rules):** candidate does NOT regress `policy_adherence` on ANY scenario.

The hard floor exists because DSPy bootstrapping is example-driven — bad examples (or examples from a stale run) can teach the model to relax a margin floor or skip a citation. Style drift is recoverable. Policy drift is not. We refuse to flip prod when the candidate beats a margin floor or skips a budget cap on any single scenario, even if it sweeps the rest.

## Cost model

- Compile (BootstrapFewShot, 4 demos): ~$0.10
- Auto-promote gate (4 scenarios × 1 mode × 2 aliases + judge calls): ~$3
- Manual review (operator promotes by hand after staging compile): ~$0.10

The gate is gated behind `--auto-promote` so casual compiles stay cheap. Operators who run `compile → staging` from the cockpit pay only the bootstrap cost; the eval gate fires only when they click `compile + auto-promote`.

## Operator surface

- `/api/dspy/agents` — list registered agents + current `prod`/`staging` aliases.
- `POST /api/dspy/optimize/{slug}?auto_promote=<bool>` — kicks off a background compile. Returns `{job_id}`.
- `GET /api/dspy/jobs/{job_id}` — poll status. Terminal status is `ok` or `error`.
- `GET /api/dspy/jobs?limit=N` — recent jobs, newest first.
- Cockpit `[REPORTS]` tab → DSPy panel renders the registry + buttons + live job status.

MLflow integration (when `MLFLOW_TRACKING_URI` is set):

- Each compile emits one MLflow run under experiment `dspy-compile` (configurable via `MLFLOW_EXPERIMENT`).
- Tags: `agent`, `status`. Metrics: `trainset_size`, `elapsed_s`. Logs `aliases.json` + the compiled prompt body.
- The cockpit's `[MLFLOW]` tab surfaces compile runs alongside operator-turn traces.

## Reset path

```bash
# Drop a compiled prompt (e.g. compiled v3 didn't pan out)
rm prompts/analyst/v3.md
# Re-point staging back at the previous version
python -c "
import json
from pathlib import Path
p = Path('prompts/analyst/aliases.json')
data = json.loads(p.read_text())
data['staging'] = 'v2'
p.write_text(json.dumps(data, indent=2) + '\n')
"
```

## What we did NOT build (yet)

- D5 (MIPROv2 + multi-step optimization for action specialists). Lifted out of the rollout because the eval harness's 4-dim rubric isn't yet calibrated for actions like `pim_enrich` or `po_held` — adding those scoring dimensions is the precondition.
- A CI gate (`.github/workflows/eval-gate.yml`) that auto-runs the eval harness on PRs touching `prompts/`. The gate logic is in place (`compare_aliases` + `dspy_optimize.run_ab_gate`); the workflow file is the next step when the project picks up CI.
- Compiled prompts for any agent other than Analyst. The registry is uniform — adding Pricing / Marketing is one row in `_agent_registry()` (`scripts/dspy_optimize.py`) plus a Signature module under `backend/app/agents/dspy_signatures/` plus a training jsonl. Defer until the Analyst loop has a full eval-harness round-trip on real data.
