# AGENTS.md

Guidance for coding agents (Claude Code, Codex, Cursor, …) working in this repository. `CLAUDE.md` just imports this file — edit here.

## Common commands

Backend (run from `backend/`):

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # requires Python >= 3.11
pip install -e ".[dev]"   # [dev] adds ruff + pyright for `make check`

# Seed mock retail spine into backend/data/spine.db
python -m app.substrate.seed

# Run API on http://127.0.0.1:8000
uvicorn app.main:app --reload

# Tests (unittest, no extra runner needed) — live in backend/tests/
python -m unittest discover -s tests -v
python -m unittest tests.test_omnichannel
python -m unittest tests.test_integrations.IntegrationLayerTest.test_mock_erpnext_sync_creates_cache_and_external_refs
```

Quality gate (run from repo root — the `Makefile` is the source of truth):

```bash
make test          # backend unittest discover (mock-only; the canonical runner)
make lint          # ruff check  (backend/app + backend/tests)
make typecheck     # pyright     (backend)
make frontend-check # tsc + vitest + vite build
make check         # lint + typecheck + test + frontend-check — run before every PR
make reset-demo    # rm spine.db, re-seed substrate, prune artifacts (the one "blow it away" path)
```

`make check` is also what CI runs (`.github/workflows/check.yml`). Two other workflows gate
PRs: `eval-gate.yml` (agent-mesh eval, `python -m tests.agents.eval.run_eval`) and
`prompt-alias-gate.yml` (prompt registry changes). The `Makefile` also has per-system Docker stacks (`make <system>-up|bootstrap|seed|nuke`
for erpnext / mautic / medusa / openboxes / akeneo / superset / mlflow / gbrain) used only
when running an integration against the real open-source system instead of mock mode.

Live integration tests are **off by default** even when `.env` has real creds. Opt in with
`RUN_LIVE_TESTS=1` (all) or `RUN_<SYSTEM>_LIVE=1` (one), or `make test-live[-<system>]`.
The gate lives in `backend/tests/_live_gate.py`; `*_live.py` test modules skip themselves
unless their env keys are set and the target is reachable.

Frontend (run from `frontend/`):

```bash
npm install
npm run dev      # http://localhost:5173, proxies /api → 127.0.0.1:8000
npm run build
npm run preview
npm run typecheck   # tsc --noEmit
npm run test        # vitest (src/__tests__/)
```

Linting/typing is `ruff` + `pyright` (backend) and `tsc` (frontend). There is no auto-formatter configured — don't add one without asking.

## Environment

`backend/app/config.py` loads `.env` from the **project root first**, then `backend/.env` (without overriding). `LLM_PROVIDER` picks one of `anthropic | openai | google`; if unset, `Settings.provider` auto-picks the first provider with an API key. Optional integration credentials (ERPNext, Mautic, Medusa, OpenBoxes, Akeneo, Superset) are listed in `backend/.env.example` — leaving them blank keeps each adapter in **mock mode**.

`POST /api/config` flips the provider at runtime by writing `LLM_PROVIDER` into `os.environ`. There is no persisted setting.

Other knobs worth knowing: `MESH_ENABLED`, `MESH_MAX_REVISION_ROUNDS`, `MESH_MAX_CRITIC_PER_DRAFT`, `MESH_TURN_TOKEN_BUDGET`, `MESH_TURN_WALLCLOCK_SECONDS` (critic/revision loop limits, `config.py`); `CORS_ORIGINS` (comma-separated, defaults to `*`); `WIKI_CURATOR_ENABLED`; `MLFLOW_TRACE_ENABLED`; `GBRAIN_BASE_URL` + `GBRAIN_BEARER` (unset → GBrain MCP client returns mocks).

## Architecture

Four-layer split mirrors the "AI Retail OS" architecture document; reading one layer alone usually misleads.

**1. Surfaces — `frontend/`**
Vite + React 18 + TS cockpit. `src/App.tsx` picks `MobileShell` or a desktop shell (via `useViewport`). Desktop layout: `StatusStrip` on top; `DataRail` (tabbed panels) · `Chat` (operator command rail, SSE consumer + agent trace) · `ApprovalRail` in the middle; `EventTape` (audit feed) at the bottom; `ApprovalDrawer` overlay. `DataRail` tabs live in `components/dataTabs/` and are driven by a single `TABS` registry — add a tab by appending one entry. Shared data comes from `DashboardProvider` (`lib/data.tsx`), which polls every 5s; `Chat` calls `bump()` on each agent event to force a refetch. `lib/api.ts` holds the typed API client — new calls should use `apiFetch<T>()` (throws `ApiError`) rather than raw `fetch`.

**2. Agent Mesh — `backend/app/agents/`**
- `base.py` — `Agent` class. One generic run-loop: call LLM → if `tool_calls`, execute Python impls → feed `tool_result` blocks back → repeat until no tool calls or `max_iters`. Yields `AgentEvent(kind, agent, data)` for streaming. `kind` ∈ {`agent_start`, `text`, `tool_call`, `tool_result`, `agent_end`, `error`}. Provider `stop_reason`s are normalized by `_classify_stop_reason` (`end_turn` / `max_tokens` / `refusal` / `other`); a truncated or refused turn ends with `agent_end` carrying `note` + `incomplete: true`. A stuck-loop guard refuses to re-execute an identical tool call more than `repeat_limit` (default 3) times.
- `chief_of_staff.py` — orchestrator. Only the operator talks to it. It owns one tool per specialist (`delegate_to_pricing`, `delegate_to_marketing`, …), each of which runs the specialist agent inline and buffers the specialist's events into an `_EventBuffer` so `run_chief()` can interleave them into the SSE stream in roughly the order they happened. With the mesh enabled, a specialist draft goes through a critique → revision loop (bounded by `MESH_*` settings and a per-turn token/wall-clock budget); a converged artifact is flipped to `stage="final"`.
- Specialists (`analyst`, `pricing`, `replenishment`, `marketing`, `merchandiser`, `fulfillment`, `store_manager`) follow a strict pattern: define `NAME`, `SYSTEM`, `TOOLS`, `IMPLS`, expose `build_agent() -> Agent`. `build_agent` passes `resolve_prompt(NAME, SYSTEM)` so the versioned prompt registry in `prompts/<slug>/vN.md` + `aliases.json` (see `prompts/README.md`, `app/llm/prompts.py`) overrides the in-code `SYSTEM`, which is only the fallback. Tools wrap helpers in `app.substrate` and `app.spine`. Specialists never delegate further.
- `critic.py` — read-only reviewer. The Chief invokes it via `delegate_to_critic` / `delegate_to_peer_review`: it re-runs the read-only spine queries behind a draft artifact and emits a structured `critique` artifact (Verified / Gaps / Risks / Counter-recommendation). It never writes substrate or mints operator-facing recommendations. `_mesh_tools.py` holds shared mesh helpers.
- Background read-only agents (not operator-chat): `wiki_curator.py` runs after each operator turn and proposes wiki edits (disable with `WIKI_CURATOR_ENABLED=0`); `improvement_auditor.py` (Track 8) runs on demand from the cockpit `[IMPROVE]` tab. Both are strictly read-only — no outbox actions, no substrate edits.
- DSPy prompt-optimization lives in `dspy_compile.py`, `dspy_dataset.py`, and `dspy_signatures/` — used to compile specialist prompts; jobs are tracked via `spine.jobs`.
- Adding a new specialist: create `agents/<name>.py` matching the pattern, then register it inside `chief_of_staff.build_orchestrator` (build the agent, add a `_delegate_to_<name>` closure, append a tool via `_build_delegate_tool`, and wire both into `tools` and `impls`). Update the Chief's `SYSTEM` prompt (and its registry version under `prompts/chief_of_staff/` if one exists) — the Chief of Staff routes purely from prompt-described capabilities.

**3. Data Spine — `backend/app/spine/`**
- `db.py` — single SQLite file at `backend/data/spine.db`; `conn()` opens a fresh connection per call. `init_db()` applies migrations on startup. The schema covers events, KG nodes/edges, retail substrate tables, action queue, policy rules, the integration tables (`integration_systems`, `sync_runs`, `external_refs`, `record_cache`, `outbox_actions`), plus jobs, wiki, and improvement-suggestion tables.
- `migrations.py` — append-only schema migrations. `MIGRATIONS` is a list of `(version, description, sql)` tuples in ascending order; `schema_version` tracks which have run; `init_db` applies unrun ones in order, one transaction per migration. **A shipped migration is frozen** — never edit it; a schema fix is a *new* migration. v1 (`BASELINE_SQL`) is all `CREATE … IF NOT EXISTS`, so adopting a pre-migrations `spine.db` is a no-op; v2 adds `background_jobs`.
- `events.py` — append-only event log. `EVENT_KINDS` is the enforced contract — `append_event` raises `ValueError` on anything else, so a new kind must be added there first (core kinds: `decision`, `action`, `observation`, `proposal`, `approval_required`, `campaign_launch`, `measurement`, `rollback`; plus `mesh_downgrade`, `wiki_*`, `brain_ingest`). Don't confuse event kinds with artifact kinds like `critique` / `report`. Treat events as the audit ground truth — Analyst measurement queries read from here.
- `artifacts.py` — markdown artifacts written to `artifacts/` with JSON frontmatter. `list_artifacts()` only returns artifacts that are referenced by an event (so deleting an event row hides the artifact even if the file exists).
- `kg.py` — toy property graph used to surface neighborhood context.
- `jobs.py` — durable SQLite-backed background-job tracking (`background_jobs` table) (replaces per-route in-memory dicts, e.g. DSPy compile). Status is one-way: `running → ok|error|cancelled`. These helpers do **not** spawn threads — the caller owns concurrency; the module only guarantees the row survives a restart and is queryable by id/kind/status.
- `wiki.py` — operator knowledge base (pages, proposals, publish state) fed by the Wiki Curator agent.
- `telemetry.py` — lightweight metric/event capture for the cockpit.

**4. Substrate — `backend/app/substrate/`**
Mocked retail systems of record. `seed.py` populates everything; `omnichannel.py` is the broad surface (categories, stores, campaigns, action queue, policy gates, KPIs); `pos.py` and `inventory.py` cover the original markdown demo path. **Tools call substrate helpers, not raw SQL.**

**5. Integrations — `backend/app/integrations/`**
Connector layer over open-source retail systems. `IntegrationAdapter` (in `base.py`) is the contract: `sync_inbound()` mirrors external records into `record_cache` + `external_refs`, `propose_outbound()` writes an `outbox_actions` row. Adapters live one-per-file in `integrations/adapters/` (ERPNext, Mautic, Medusa, OpenBoxes, Akeneo, Superset, Shopify), each declaring `env_keys`; `adapters/__init__.py` owns the canonical `ADAPTERS` list (`systems.py` is only a back-compat re-export shim — import from the adapter modules in new code), `http.py` holds the shared `JsonHttpClient`, `store.py` the cache/ref persistence helpers. If any required env key is missing, the adapter runs in **mock mode**: it mirrors seeded substrate data into the cache and never mutates the external system. `registry.py` is the public façade used by `main.py` and `omnichannel.py`. **All outbound writes are approval-gated** — `apply_outbound` lives once in the `IntegrationAdapter` base and returns `applied_mock` (no creds) or `draft_created` (creds present, but writes are still drafts only); a failed live dispatch lands the row in `error`. To add a live outbound action, add its type to the adapter's `LIVE_ACTION_TYPES` and handle it in `_dispatch_outbound` — don't override `apply_outbound`.

**LLM provider abstraction — `backend/app/llm/`**
`base.py` defines unified `Tool`, `Message`, `ToolCall`, `AssistantTurn`, `LLMProvider` (Protocol). The three impls (`anthropic_p.py`, `openai_p.py`, `google_p.py`) translate the unified shape to/from the native tool-use protocol of each SDK. Agents are written once against `LLMProvider`. When adding a feature that touches LLM I/O, change all three impls or none. Also here: `prompts.py` (prompt registry resolver), `tracing.py` (optional MLflow tracing, no-op unless `MLFLOW_TRACE_ENABLED` and `mlflow` is installed), `mcp.py` (GBrain MCP client).

## Cross-layer invariants

- The Chief of Staff is the **only** agent the operator chats with. It must delegate at least one specialist before answering — even simple questions go to the Analyst.
- An `Agent` never imports another `Agent`. Specialists are composed only inside `chief_of_staff.build_orchestrator`.
- Anything that would mutate an external system must go through `integration_registry.propose_outbound()` first and live in `outbox_actions` until `apply_outbound` is called.
- Every meaningful agent action should append to the event log; user-visible reports should be persisted as artifacts and referenced from an event so they show up in the audit rail.
- Layer imports point downward only: `substrate` / `spine` / `integrations` / `llm` must not import from `agents` or `routes`, and `agents` must not import from `routes`. Enforced by `tests/test_architecture.py`.
- Tests use `tempfile.TemporaryDirectory` and patch `db.DB_PATH` rather than mocking SQLite — keep new tests on this pattern so the real schema is exercised.

## Frontend ↔ backend contract

`frontend/vite.config.ts` proxies `/api/*` to `127.0.0.1:8000`, so the frontend uses relative URLs. The chat consumes SSE from `POST /api/chat` (event name `agent`, JSON body `{kind, agent, data}` matching `AgentEvent`). All other panels poll plain JSON GET endpoints listed in `README.md`.

`main.py` is **wiring only** — it builds the app, runs the startup hook (`init_db` + `integration_registry.refresh_systems`), and mounts routers. Every endpoint lives in `app/routes/<module>.py` (`core`, `chat`, `mesh`, `integrations`, `brain`, `dspy`, `mlflow`, `wiki`, `improvements`). Add a new domain surface as a new route module registered in `routes/__init__.py` and mounted in `main.py` — don't drop endpoints into `main.py` or an unrelated module.

## What is intentionally stubbed

See `docs/PRODUCTION-READINESS.md` for the full prototype → production boundary.

External write-back without approval (every outbound stays a draft even with creds), a real loop scheduler (cron-replaced by tool-driven measurement), an editable Policy & Spec Registry (currently embedded in agent system prompts), auth, multi-tenancy. Don't add these without an explicit ask. (Note: the integration adapters are real — they hit live ERPNext/Mautic/etc. when env keys are present; **mock mode is the default**, not a permanent stub.)
