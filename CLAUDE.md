# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

Backend (run from `backend/`):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Seed mock retail spine into backend/data/spine.db
python -m app.substrate.seed

# Run API on http://127.0.0.1:8000
uvicorn app.main:app --reload

# Tests (unittest, no extra runner needed)
python -m unittest discover -s tests -v
python -m unittest tests.test_omnichannel
python -m unittest tests.test_integrations.IntegrationLayerTest.test_mock_erpnext_sync_creates_cache_and_external_refs
```

Frontend (run from `frontend/`):

```bash
npm install
npm run dev      # http://localhost:5173, proxies /api → 127.0.0.1:8000
npm run build
npm run preview
```

The repo has no linter or formatter configured. Don't add one without asking.

## Environment

`backend/app/config.py` loads `.env` from the **project root first**, then `backend/.env` (without overriding). `LLM_PROVIDER` picks one of `anthropic | openai | google`; if unset, `Settings.provider` auto-picks the first provider with an API key. Optional integration credentials (ERPNext, Mautic, Medusa, OpenBoxes, Akeneo, Superset) are listed in `backend/.env.example` — leaving them blank keeps each adapter in **mock mode**.

`POST /api/config` flips the provider at runtime by writing `LLM_PROVIDER` into `os.environ`. There is no persisted setting.

## Architecture

Four-layer split mirrors the "AI Retail OS" architecture document; reading one layer alone usually misleads.

**1. Surfaces — `frontend/`**
Vite + React 18 + TS. Single page in `src/App.tsx` with three rails: `Dashboard` (cockpit KPIs), `Chat` (operator command rail, SSE consumer), `EventLog` + `Artifacts` (audit rail). `Chat` triggers `setRefreshKey` on each agent event so the other panels re-fetch.

**2. Agent Mesh — `backend/app/agents/`**
- `base.py` — `Agent` class. One generic run-loop: call LLM → if `tool_calls`, execute Python impls → feed `tool_result` blocks back → repeat until no tool calls or `max_iters`. Yields `AgentEvent(kind, agent, data)` for streaming. `kind` ∈ {`agent_start`, `text`, `tool_call`, `tool_result`, `agent_end`, `error`}.
- `chief_of_staff.py` — orchestrator. Only the operator talks to it. It owns one tool per specialist (`delegate_to_pricing`, `delegate_to_marketing`, …), each of which runs the specialist agent inline and buffers the specialist's events into an `_EventBuffer` so `run_chief()` can interleave them into the SSE stream in roughly the order they happened.
- Specialists (`analyst`, `pricing`, `replenishment`, `marketing`, `merchandiser`, `fulfillment`, `store_manager`) follow a strict pattern: define `NAME`, `SYSTEM`, `TOOLS`, `IMPLS`, expose `build_agent() -> Agent`. Tools wrap helpers in `app.substrate` and `app.spine`. Specialists never delegate further.
- Adding a new specialist: create `agents/<name>.py` matching the pattern, then register it inside `chief_of_staff.build_orchestrator` (build the agent, add a `_delegate_to_<name>` closure, append a tool via `_build_delegate_tool`, and wire both into `tools` and `impls`). Update the `SYSTEM` prompt — the Chief of Staff routes purely from prompt-described capabilities.

**3. Data Spine — `backend/app/spine/`**
- `db.py` — single SQLite file at `backend/data/spine.db`. The schema is the canonical contract: events, KG nodes/edges, retail substrate tables, action queue, policy rules, and the integration tables (`integration_systems`, `sync_runs`, `external_refs`, `record_cache`, `outbox_actions`). All schema changes go here — there are no migrations; add `CREATE TABLE IF NOT EXISTS` and re-init.
- `events.py` — append-only event log. Event `kind` values used across agents/tools: `decision`, `action`, `observation`, `proposal`, `approval_required`, `campaign_launch`, `measurement`, `rollback`. Treat these as the audit ground truth — Analyst measurement queries read from here.
- `artifacts.py` — markdown artifacts written to `artifacts/` with JSON frontmatter. `list_artifacts()` only returns artifacts that are referenced by an event (so deleting an event row hides the artifact even if the file exists).
- `kg.py` — toy property graph used to surface neighborhood context.

**4. Substrate — `backend/app/substrate/`**
Mocked retail systems of record. `seed.py` populates everything; `omnichannel.py` is the broad surface (categories, stores, campaigns, action queue, policy gates, KPIs); `pos.py` and `inventory.py` cover the original markdown demo path. **Tools call substrate helpers, not raw SQL.**

**5. Integrations — `backend/app/integrations/`**
Connector layer over open-source retail systems. `IntegrationAdapter` (in `base.py`) is the contract: `sync_inbound()` mirrors external records into `record_cache` + `external_refs`, `propose_outbound()` writes an `outbox_actions` row. Adapters in `systems.py` (ERPNext, Mautic, Medusa, OpenBoxes, Akeneo, Superset) — each declares `env_keys`. If any required env key is missing, the adapter runs in **mock mode**: it mirrors seeded substrate data into the cache and never mutates the external system. `registry.py` is the public façade used by `main.py` and `omnichannel.py`. **All outbound writes are approval-gated** — `apply_outbound` returns `applied_mock` (no creds) or `draft_created` (creds present, but writes are still drafts only).

**LLM provider abstraction — `backend/app/llm/`**
`base.py` defines unified `Tool`, `Message`, `ToolCall`, `AssistantTurn`, `LLMProvider` (Protocol). The three impls (`anthropic_p.py`, `openai_p.py`, `google_p.py`) translate the unified shape to/from the native tool-use protocol of each SDK. Agents are written once against `LLMProvider`. When adding a feature that touches LLM I/O, change all three impls or none.

## Cross-layer invariants

- The Chief of Staff is the **only** agent the operator chats with. It must delegate at least one specialist before answering — even simple questions go to the Analyst.
- An `Agent` never imports another `Agent`. Specialists are composed only inside `chief_of_staff.build_orchestrator`.
- Anything that would mutate an external system must go through `integration_registry.propose_outbound()` first and live in `outbox_actions` until `apply_outbound` is called.
- Every meaningful agent action should append to the event log; user-visible reports should be persisted as artifacts and referenced from an event so they show up in the audit rail.
- Tests use `tempfile.TemporaryDirectory` and patch `db.DB_PATH` rather than mocking SQLite — keep new tests on this pattern so the real schema is exercised.

## Frontend ↔ backend contract

`frontend/vite.config.ts` proxies `/api/*` to `127.0.0.1:8000`, so the frontend uses relative URLs. The chat consumes SSE from `POST /api/chat` (event name `agent`, JSON body `{kind, agent, data}` matching `AgentEvent`). All other panels poll plain JSON GET endpoints listed in `README.md`.

## What is intentionally stubbed

Real POS/OMS/WMS/CRM/EDI integrations, external write-back without approval, a real loop scheduler (cron-replaced by tool-driven measurement), an editable Policy & Spec Registry (currently embedded in agent system prompts), auth, multi-tenancy. Don't add these without an explicit ask.
