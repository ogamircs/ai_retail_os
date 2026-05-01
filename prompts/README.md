# Prompt registry (Track 4 M4)

Versioned agent system prompts, alias-resolved at runtime by `app.llm.prompts.resolve_prompt`. Flipping an alias changes the next operator turn's behaviour without a code deploy.

## Layout

```
prompts/
  README.md
  <agent-slug>/
    v1.md
    v2.md
    aliases.json   {"prod": "v1", "staging": "v2"}
```

`<agent-slug>` is the lower-snake-case form of the agent name. Examples:

| Agent name        | Slug             |
|-------------------|------------------|
| Analyst           | `analyst`        |
| Pricing & Promo   | `pricing_promo`  |
| Marketing         | `marketing`      |
| Replenishment     | `replenishment`  |
| Merchandiser      | `merchandiser`   |
| Fulfillment       | `fulfillment`    |
| Store Manager     | `store_manager`  |
| Critic            | `critic`         |
| Chief of Staff    | `chief_of_staff` |

## Resolution chain

For each `build_agent()` call (every operator turn), `resolve_prompt(name, fallback=SYSTEM_in_code)` resolves in this order:

1. **`<AGENT>_PROMPT_OVERRIDE`** env var — if set to a file path, load that file directly. Useful for one-off A/B tests without committing a registry entry.
2. **`<AGENT>_PROMPT_ALIAS`** env var — if set to e.g. `staging`, look up `aliases.json[<that>]` and load `prompts/<slug>/<version>.md`.
3. **`prod`** alias from `aliases.json` — the default production path.
4. **In-code `SYSTEM` constant** — fallback so the cockpit demo runs identically when the registry is empty (e.g. fresh checkout).

Env-var slug rule: `Pricing & Promo` → `PRICING_PROMO_PROMPT_ALIAS`.

## Usage examples

Promote a staging prompt to prod:

```bash
# Edit aliases.json:
#   { "prod": "v3", "staging": "v3" }
# Next operator turn picks up v3. No deploy.
```

A/B test a tweaked prompt for a single shell session:

```bash
ANALYST_PROMPT_OVERRIDE=/tmp/my-experiment.md uvicorn app.main:app --reload
```

Switch staging → prod for one turn (cockpit's `LLM_PROVIDER` flip pattern):

```bash
PRICING_PROMO_PROMPT_ALIAS=staging python -c "..."
```

## How to add a new version

1. `cp prompts/<slug>/v<n>.md prompts/<slug>/v<n+1>.md`
2. Edit the new file.
3. Run the eval harness: `RUN_EVAL=1 ANTHROPIC_API_KEY=... python -m tests.agents.eval.run_eval`
4. Compare in MLflow (Track 4 M3 surfaces both runs side-by-side).
5. If the new version wins, update `aliases.json` to point `prod` at it. Commit.
6. If it loses, keep it under `staging` for follow-up tuning, or delete it.

## Operator notes

- The registry is **read-only at runtime**. Agents never write here.
- Older versions are kept for audit / rollback — `aliases.json` always names the canonical pointer.
- The Track 4 CI gate (M6) runs the eval harness on any PR that touches `prompts/` or `backend/app/agents/` and posts a delta comment.
