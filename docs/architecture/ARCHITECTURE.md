# AI Retail OS — Architecture

> Status: as of 2026-05-02. Reflects every shipped track (T1–T7).
> If this doc disagrees with the code, the code is right — open a PR
> to fix the doc.

This document is the load-bearing reference for how the cockpit fits
together. Read it before non-trivial changes. The high-level
[`CLAUDE.md`](../../CLAUDE.md) is the elevator pitch; this is the
floor plan.

---

## 1. System overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         BROWSER (Vite + React)                       │
│  StatusStrip · Chat · ApprovalRail · DataRail (8 tabs) · EventTape   │
└────────────┬─────────────────────────────────────┬───────────────────┘
             │ /api/chat (SSE)        /api/* (REST)│
             ▼                                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      FASTAPI (uvicorn, sync threadpool)              │
│  38 routes · main.py orchestrates · SSE producer thread per chat     │
└────────────┬───────────────────────┬────────────────────────────┬────┘
             │                       │                            │
             ▼                       ▼                            ▼
   ┌──────────────────┐   ┌────────────────────┐   ┌─────────────────────┐
   │   Agent Mesh     │   │   Data Spine       │   │  Integrations       │
   │                  │◄─►│  (SQLite single    │◄─►│  (mock-or-live      │
   │  Chief + 7 spec  │   │   file: spine.db)  │   │   adapters per      │
   │  + Critic +      │   │                    │   │   external system)  │
   │  Wiki Curator    │   │                    │   │                     │
   └────────┬─────────┘   └─────────┬──────────┘   └─────────────────────┘
            │                       │
            │ tools: substrate +    │
            │ wiki + brain + spine  │
            ▼                       ▼
   ┌──────────────────┐   ┌────────────────────┐
   │    Substrate     │   │     Wiki + Brain   │
   │  (mock retail    │   │  agent memory      │
   │   systems of     │   │  (Track 5/6)       │
   │   record)        │   │                    │
   └──────────────────┘   └────────────────────┘

   Side cars:
     LLM provider abstraction (Anthropic / OpenAI / Gemini)
     MLflow tracing (Track 4 — optional dep)
     DSPy compile pipeline (Track 7 — optional dep)
     GBrain MCP (Track 6 — mock-by-default HTTP client)
```

**One process, one SQLite file.** No message bus, no Redis, no
external workers. Concurrency comes from FastAPI's threadpool + the
agents' inline tool execution. The single `spine.db` SQLite file is
the canonical state. This is intentional: the cockpit is a prototype
demonstrating an architecture, not a multi-tenant SaaS.

---

## 2. Six layers, one direction of dependency

```
SURFACES   (frontend/)            ← can read everything below via /api/*
  │
  ▼
ROUTES     (main.py)              ← thin glue; no business logic
  │
  ▼
AGENT MESH (app/agents/)          ← orchestration + LLM calls + tool dispatch
  │
  ▼
SPINE      (app/spine/)           ← events, artifacts, KG, wiki — write here
  │
  ▼
SUBSTRATE  (app/substrate/)       ← mock retail systems of record — read here
  │
  ▼
INTEGRATIONS (app/integrations/)  ← real external systems behind adapters

LLM provider abstraction (app/llm/) is orthogonal — every layer can
import it but it imports nothing from layers above.
```

**Direction matters.** Agents call substrate helpers and spine
writes. Substrate never imports agents. Spine never imports
substrate. The integration layer never imports either — it only
mirrors data through `record_cache` + `external_refs` and queues
writes through `outbox_actions`.

**The Chief of Staff is the only agent the operator chats with.** Every
specialist sits behind a `delegate_to_<name>` tool the Chief owns.
Specialists never delegate further; they call substrate / spine /
wiki / brain helpers and produce one or more artifacts.

---

## 3. Data Spine — `backend/app/spine/`

Single SQLite file at `backend/data/spine.db`. The schema in `db.py`
is the canonical contract; all schema changes go there with `CREATE
TABLE IF NOT EXISTS` and an `init_db()` re-run. **There are no
migrations** — this is a prototype.

### 3.1 Tables

Three families of tables, by purpose:

**Audit + agent state** (the spine proper):

| Table              | What's in it                                              |
|--------------------|-----------------------------------------------------------|
| `events`           | Append-only event log. Every meaningful agent action      |
|                    | lands here with `(ts, agent, kind, sku?, payload_json,    |
|                    | artifact_id?)`. Source of truth for measurement and audit.|
| `kg_nodes`         | Toy property graph nodes — used by the KG-neighborhood    |
|                    | tool to surface category/SKU/store relationships.         |
| `kg_edges`         | Toy property graph edges with `(src, rel, dst)` PK.       |
| `wiki_pages`       | Latest revision per slug. `status ∈ {draft, published,    |
|                    | deprecated}`. Read path is O(1) (no joins). Track 5.      |
| `wiki_revisions`   | Full version history per slug. Track 5.                   |
| `improvement_runs` | One row per `audit now` invocation. Status: running →     |
|                    | ok / error. Track 8.                                      |
| `improvement_     `| 3-8 rows per run. severity ∈ {high, medium, low}, area    |
| `suggestions`      | enum (pricing / replenishment / wiki_coverage / …),       |
|                    | status ∈ {open, accepted, dismissed}. Track 8.            |

**Substrate** (mock retail systems of record — populated by `seed.py`):

| Table                          | What's in it                              |
|--------------------------------|-------------------------------------------|
| `substrate_skus`               | SKU + name + category + vendor.           |
| `substrate_inventory`          | Per-SKU on_hand, reorder point, prices.   |
| `substrate_sales`              | 90-day daily sales rows.                  |
| `substrate_categories`         | Category lifecycle, margin, weather etc.  |
| `substrate_stores`             | 5 stores: capacity, labor, demand signals.|
| `substrate_store_inventory`    | (store, sku) on_hand + capacity.          |
| `substrate_customer_segments`  | Segment metadata.                         |
| `substrate_segment_affinity`   | Segment × category affinity scores.       |
| `substrate_orders`             | Recent omnichannel orders.                |
| `substrate_inbound_pos`        | Inbound POs with vendor risk drivers.     |
| `substrate_campaigns`          | Marketing campaigns + KPIs.               |

**Action plumbing**:

| Table                | What's in it                                            |
|----------------------|---------------------------------------------------------|
| `action_queue`       | High-level proposed actions surfaced to the operator.   |
| `policy_rules`       | Policy floors (margin, discount cap, etc).              |
| `integration_systems`| One row per external system + last sync metadata.       |
| `sync_runs`          | One row per `sync_inbound()` call (status, counts, ts). |
| `external_refs`      | (system, domain, local_id) → external_id mapping.       |
| `record_cache`       | Cached external rows from inbound sync.                 |
| `outbox_actions`     | Approval-gated outbound writes — `pending` → `draft_    |
|                      | created` (live) or `applied_mock` (no creds).           |

### 3.2 Event kinds

The append-only event log carries a small fixed vocabulary of
`kind` values. Treat these as the audit ground truth — Analyst
measurement queries read here.

| Kind                  | Source             | Meaning                              |
|-----------------------|--------------------|--------------------------------------|
| `decision`            | any specialist     | A decision was made on a SKU/category|
| `action`              | action specialists | An action was applied (markdown etc) |
| `observation`         | Analyst, Critic    | A measurement / audit finding        |
| `proposal`            | action specialists | A draft outbox action was queued     |
| `approval_required`   | integrations layer | Operator approval needed             |
| `campaign_launch`     | Marketing          | Campaign kicked off                  |
| `campaign_brief`      | Marketing          | Campaign brief written               |
| `campaign_measurement`| Analyst            | Campaign result measured             |
| `rollback`            | any                | Revert of a prior action             |
| `wiki_edit`           | any                | Draft revision created (Track 5 W3)  |
| `wiki_publish`        | Wiki Curator / op  | Draft promoted (W4)                  |
| `wiki_deprecate`      | operator           | Page retired                         |
| `brain_ingest`        | Chief of Staff     | Turn ingested into GBrain (Track 6)  |
| `mesh_downgrade`      | Chief of Staff     | Review loop hit token/wallclock cap  |
| `observation`         | Improvement Auditor| Audit run summary (Track 8)          |

### 3.3 Per-turn id propagation (T5 W4 + T6 G3)

`current_turn_id: ContextVar[str | None]` lives in `events.py`. The
chat endpoint sets it at the entry point of `run_chief`; every
`append_event` call auto-stamps `payload.turn_id`. Helpers
`events_for_turn(turn_id, since_ts=None)` and `events_since_ts(...)`
are the read paths.

This solves a real correctness bug: two concurrent `/api/chat` turns
share the wall-clock window, and a wall-clock-only filter would let
turn A's clean Critic auto-publish turn B's wiki drafts. The
contextvar gives every turn an isolated event scope.

### 3.4 Artifacts — `app/spine/artifacts.py`

Markdown artifacts written to `artifacts/` with JSON frontmatter.
`list_artifacts()` returns artifacts that are *referenced by an
event* (so deleting an event row hides the artifact even if the
file exists). The `stage` field cycles `draft → critique → revision
→ peer_review → final` per the Track 2 mesh.

### 3.5 KG — `app/spine/kg.py`

Toy property graph used by the `kg_neighborhood` tool. SKU/category/
store nodes with `lives_in` / `belongs_to` edges. Useful for the
Analyst when answering "what's near this SKU?" without joining 5
substrate tables.

---

## 4. Substrate — `backend/app/substrate/`

Mock retail systems of record. This is the layer the agents
*read* from when they need data. Critically, **tools call substrate
helpers, not raw SQL** — `omnichannel.get_kpis()`, not
`SELECT … FROM substrate_orders`. This keeps the schema isolated.

| Module           | Surface                                                      |
|------------------|--------------------------------------------------------------|
| `seed.py`        | `seed()` populates everything. Idempotent on re-run.         |
| `omnichannel.py` | The broad surface: KPIs, categories, stores, campaigns,      |
|                  | orders, action queue, policy gates. Used by every specialist.|
| `pos.py`         | Sales + transactions.                                        |
| `inventory.py`   | SKU + per-store inventory.                                   |

The `seed.py` populates 30 SKUs across 3 categories, 5 stores, 90
days of sales, 5 customer segments, recent orders, inbound POs,
and 1+ marketing campaigns — enough to drive every demo path.

---

## 5. Agent Mesh — `backend/app/agents/`

### 5.1 The base loop — `base.py`

`Agent.run(user_input, llm) → Iterator[AgentEvent]`. One generic
loop:

```
agent_start
↓
  call LLM with (system_prompt, messages, tools)
  ↓
  if turn.text:           yield text event
  if no tool_calls:       yield agent_end, return
  for each tool_call:
    yield tool_call
    result = impl(input)  (or {"error": "unknown tool"})
    yield tool_result
  append tool results to messages, loop
↓
agent_end (or "max iterations reached" after `max_iters` rounds)
```

Streamed events have `kind ∈ {agent_start, text, tool_call, tool_
result, agent_end, error}`. The chat endpoint forwards them as SSE.

### 5.2 The Chief of Staff — `chief_of_staff.py`

The only agent the operator talks to. The Chief owns:

- One `delegate_to_<specialist>` tool per specialist (7 total) plus
  `delegate_to_critic` (audit) and `delegate_to_peer_review`
  (cross-domain second opinion, Track 2 A3).
- `_run_delegate_with_review` — Track 2 A2 review loop. The Chief
  calls a specialist; the specialist returns a `draft`; the Chief
  invokes the Critic; if the critique surfaces real Risks/Gaps, the
  Chief tasks the original specialist to revise (up to
  `MESH_MAX_REVISION_ROUNDS`); the converged artifact is flipped to
  `stage='final'` in place via `update_artifact_stage`.
- An `_EventBuffer` sink so events from delegated specialists
  interleave into the SSE stream in roughly the order they happen.
- The post-turn pipeline: Critic-gated wiki auto-publish (W4) →
  Wiki Curator (W6) → GBrain ingest hook (G3). All wrapped in an
  outer `try/finally` that resets `current_turn_id` so a slow
  curator can't corrupt a future request's scope.

### 5.3 The seven specialists

| Slug              | NAME              | Lane                                                         |
|-------------------|-------------------|--------------------------------------------------------------|
| `analyst`         | Analyst           | Read-only measurement / reporting. Skips review loop.        |
| `pricing_promo`   | Pricing & Promo   | Markdowns, promos, price experiments.                        |
| `replenishment`   | Replenishment     | POs, holds, expedites.                                       |
| `marketing`       | Marketing         | Campaigns, briefs, segments, weather plays.                  |
| `merchandiser`    | Merchandiser      | Lifecycle, allocation, store transfers.                      |
| `fulfillment`     | Fulfillment       | BOPIS, ship-from-store, DC routing.                          |
| `store_manager`   | Store Manager     | Store execution, labor, capacity exceptions.                 |
| `critic`          | Critic            | Read-only auditor. Produces `kind="critique"` artifacts.     |
| `wiki_curator`    | Wiki Curator      | Post-turn observer (Track 5 W6). Read-only over the turn's   |
|                   |                   | events; proposes wiki drafts (rate-limited 3/turn, 1/slug/24h)|

Every specialist follows the same pattern: define `NAME`, `SYSTEM`,
`TOOLS`, `IMPLS`, expose `build_agent() -> Agent`. Action specialists
also get the mesh tools (`read_artifact`, stage-aware `write_artifact`,
`peer_review`) and the wiki write surface (`wiki_propose_edit`).
Read-only specialists (Analyst, Critic) get the brain tools
(`brain_search`, `brain_read`, `brain_query`) and the Critic also
gets the code-graph tools (`code_callers`, `code_callees`, `code_def`,
`code_refs`).

### 5.4 The mesh tool kit — `_mesh_tools.py`

Shared tool builders so every specialist sees the same surface:

- `read_artifact` — load any artifact body + metadata by id.
- `write_artifact` (stage-aware) — first emission is `draft`, revision
  rounds set `stage='revision'` with `refs=[original, critique]`,
  peer reviews set `stage='peer_review'`. The Chief flips converged
  output to `final` via `artifacts.update_artifact_stage`.
- `wiki_search` / `wiki_read` — read-only wiki access. Defaults to
  `status='published'` so unreviewed drafts don't leak into agent
  context as evidence.
- `wiki_propose_edit` — write a learning as a draft (action specialists
  only).
- `brain_search` / `brain_read` / `brain_query` — GBrain MCP wrappers.
  Mock-mode falls back to wiki content; live-mode hits
  `gbrain serve --http`.
- `code_callers` / `code_callees` / `code_def` / `code_refs` — Critic-
  only. Mock-mode returns `{mock: true, results: []}` so the Critic
  surfaces the gap rather than hallucinating.

### 5.5 Adding a new specialist

1. Create `app/agents/<name>.py` matching the pattern (NAME / SYSTEM /
   TOOLS / IMPLS / `build_agent()`).
2. In `chief_of_staff.build_orchestrator`: build the agent, add a
   `_delegate_to_<name>` closure that invokes
   `_run_delegate_with_review` (or skips it if read-only), append a
   `_build_delegate_tool("delegate_to_<name>", "<NAME>")` entry to
   `tools`, register the closure in `impls`.
3. Update the Chief's `SYSTEM` prompt — the Chief routes purely
   from prompt-described capabilities.

---

## 6. LLM provider abstraction — `backend/app/llm/`

| File             | Purpose                                                        |
|------------------|----------------------------------------------------------------|
| `base.py`        | Unified `Tool`, `Message`, `ToolCall`, `AssistantTurn`, and    |
|                  | `LLMProvider` Protocol.                                        |
| `anthropic_p.py` | Translates unified shape ↔ Anthropic tool-use protocol.        |
| `openai_p.py`    | Same, OpenAI Chat Completions.                                 |
| `google_p.py`    | Same, Gemini.                                                  |
| `prompts.py`     | Prompt registry (Track 4 M4): version-pinned per agent under   |
|                  | `prompts/<slug>/v<n>.md`, alias-resolved at runtime.           |
| `tracing.py`     | MLflow instrumentation (Track 4 M2). Lazy import — no-op when  |
|                  | `MLFLOW_TRACE_ENABLED` is unset.                               |
| `mcp.py`         | GBrain HTTP client (Track 6 G2). Mock-by-default.              |

`get_provider()` reads `LLM_PROVIDER` (or auto-picks from env keys),
returns the wrapped (tracing-aware) provider. Agents are written
once against the `LLMProvider` Protocol — change a feature that
touches LLM I/O? Update all three impls or none.

### 6.1 Prompt registry (Track 4 M4)

```
prompts/
  README.md
  <agent-slug>/
    v1.md
    v2.md
    aliases.json   { "prod": "v1", "staging": "v2" }
  training/
    analyst.jsonl
    pricing_promo.jsonl
```

Resolution chain (first hit wins):
1. `<AGENT>_PROMPT_OVERRIDE` env → load that file directly.
2. `<AGENT>_PROMPT_ALIAS` env → look up in `aliases.json`.
3. Default `prod` alias from `aliases.json`.
4. The in-code `SYSTEM` constant the agent module exposes.

Flipping `aliases.json["prod"]` changes the next operator turn's
behaviour without a code deploy. The Track 4 M6 + Track 7 D7 CI
gates compare prod vs staging on the eval harness before any
auto-promotion.

---

## 7. Integrations — `backend/app/integrations/`

Connector layer over open-source retail systems. Six adapters
shipped: ERPNext, Mautic, Medusa, OpenBoxes, Akeneo, Superset.

### 7.1 The contract — `IntegrationAdapter` (in `base.py`)

```python
class IntegrationAdapter:
    definition: IntegrationDefinition  # system_id, env_keys, ...

    def configured(self) -> bool                    # all env_keys non-empty
    def system_row(self) -> dict                    # cockpit row
    def healthcheck(self) -> IntegrationResult
    def sync_inbound(self) -> IntegrationResult     # mirror real → record_cache
    def propose_outbound(...) -> dict               # queue an outbox action
    def apply_outbound(action_id) -> dict           # called by /api/.../apply
```

`configured()` returns False when any required env key is missing.
In that case the adapter runs in **mock mode**: it mirrors seeded
substrate data into `record_cache` and never mutates the external
system. With creds, `apply_outbound` writes drafts to the real
system (e.g. an ERPNext Pricing Rule, a Mautic Campaign).

**All outbound writes are approval-gated.** `propose_outbound`
writes a row to `outbox_actions` with `status='pending'`; the
operator clicks Apply in the cockpit; that calls `apply_outbound`
which returns either `applied_mock` (no creds) or `draft_created`
(creds, but real-system writes are still drafts only — never auto-
shipped).

### 7.2 The registry — `registry.py`

Public façade used by `main.py`, `omnichannel.py`, and any code
that wants to do something across all systems. `list_systems()`,
`get(system_id)`, `sync_all()`, `apply_outbound(system_id, action_id)`.

### 7.3 The store — `store.py`

DAL over `outbox_actions`, `external_refs`, `record_cache`, `sync_runs`.
Adapters never touch SQLite directly — they call `store.create_outbox_
action(...)` and friends.

### 7.4 Per-adapter shape

Each of the six adapters in `systems.py` follows this template:

```python
class <System>Adapter(IntegrationAdapter):
    definition = IntegrationDefinition(env_keys=("<SYSTEM>_BASE_URL", ...))

    def configured(self) -> bool:           # additional auth shape
    def _client(self) -> JsonHttpClient:    # auth header
    def _live_sync(self) -> IntegrationResult:
    def _mock_sync(self) -> IntegrationResult:
    def sync_inbound(self) -> IntegrationResult:
        return self._live_sync() if self.configured() else self._mock_sync()

    LIVE_ACTION_TYPES = {"<types>"}
    def apply_outbound(self, action_id):    # dispatches per-action helpers
```

Each adapter has env-gated live tests in `tests/test_integrations_
<system>_live.py` that skip automatically without creds.

---

## 8. Frontend — `frontend/`

Vite + React 18 + TypeScript. Single-page console.

### 8.1 Layout (`App.tsx`)

```
┌───────────────────────────────────────────────────────────┐
│ StatusStrip   provider · clock · KPIs · chips · APPROVE n │
├───────────────────────────────────────────────────────────┤
│                                                           │
│ ┌────────────────┐  ┌──────────────┐  ┌────────────────┐  │
│ │   DataRail     │  │     Chat     │  │ ApprovalRail   │  │
│ │ (8 tabs)       │  │  (operator)  │  │   + Artifacts  │  │
│ │                │  │              │  │                │  │
│ │ CATEGORIES     │  │  SSE stream  │  │  Pending       │  │
│ │ STORES         │  │  per turn    │  │  Drafts        │  │
│ │ INVENTORY      │  │              │  │                │  │
│ │ CAMPAIGNS      │  │              │  │                │  │
│ │ INTEGRATIONS   │  │              │  │                │  │
│ │ REPORTS+DSPy   │  │              │  │                │  │
│ │ WIKI           │  │              │  │                │  │
│ │ BRAIN          │  │              │  │                │  │
│ │ MLFLOW         │  │              │  │                │  │
│ └────────────────┘  └──────────────┘  └────────────────┘  │
├───────────────────────────────────────────────────────────┤
│                  EventTape (live spine events)            │
└───────────────────────────────────────────────────────────┘
```

`MobileShell.tsx` is the responsive variant that collapses the rails
into vertical sections.

### 8.2 Data flow

- `lib/data.tsx` — `useDashboardData` hook polls every 5s.
  Fans out to `getKpis`, `listCategories`, `listCampaigns`,
  `listStores`, `getInventoryHealth`, `listOrders`, `listEvents`,
  `listArtifacts`, `listActionQueue`, `listIntegrationSystems`. One
  hook drives every tab + the StatusStrip KPIs.
- `lib/api.ts` — typed wrappers. `chatStream(...)` is the only SSE
  caller (`POST /api/chat`).
- `lib/drawerContext.tsx` — global context for the right-side drawer
  (artifact preview, approval drawer).
- `lib/eventCache.ts` — in-memory dedup so the EventTape doesn't
  flicker on poll.
- `lib/safeMarkdown.ts` — `marked.parse` wrapped by DOMPurify with a
  strict allowlist (no `script`/`iframe`/`on*=`). Used by WikiTab,
  BrainTab, ApprovalDrawer.
- `lib/pushNotifier.ts` — Track 3 B3. Watches the action queue for
  new `approval_required` rows and fires an OS-level notification
  via Tauri when the shell is native, falling back to
  `window.Notification` in the browser.

### 8.3 Tab → backend route map

| Tab          | Polled routes                                                |
|--------------|--------------------------------------------------------------|
| CATEGORIES   | `/api/categories`                                            |
| STORES       | `/api/stores`                                                |
| INVENTORY    | substrate via `/api/kpis` (skus list embedded)               |
| CAMPAIGNS    | `/api/marketing/campaigns`                                   |
| INTEGRATIONS | `/api/integrations/systems` + `/api/integrations/sync-runs`  |
| REPORTS      | `/api/artifacts` + DSPy panel polls `/api/dspy/agents`       |
|              | + `/api/dspy/jobs/{id}` while a compile is in flight         |
| WIKI         | `/api/wiki/pages` + `/api/wiki/search` + `/api/wiki/pinned`  |
| BRAIN        | `/api/brain/status` + `/api/brain/search` + `/api/brain/     |
|              | pages/{slug}`                                                |
| MLFLOW       | `/api/mlflow/status` (when `MLFLOW_TRACKING_URI` set)        |
| IMPROVE      | `/api/improvements/runs` + `/api/improvements/suggestions`   |
|              | + accept/dismiss POST routes                                 |

---

## 9. End-to-end request flow — operator → action

The motivating example: operator types "Summer apparel sell-through
collapsed; build a markdown plan."

```
Browser                         FastAPI                  Agent Mesh                Spine
   │                               │                         │                       │
   │ POST /api/chat ───────────────▶                         │                       │
   │                               │ get_provider()          │                       │
   │                               │ run_chief(input, llm)   │                       │
   │                               │   set current_turn_id   │                       │
   │                               │   ┌─────────────────────▶ Chief.run             │
   │                               │   │                     │   delegate_to_pricing │
   │                               │   │                     │   ┌─────▶ Pricing.run │
   │                               │   │                     │   │   query_sales()   │
   │                               │   │                     │   │   list_skus()     │
   │                               │   │                     │   │   write_artifact  │ → events: observation
   │                               │   │                     │   │      (draft)      │   artifact: draft
   │ ◄─── SSE: tool_call ─────────────│ ◄────────────────────┼───┘                   │
   │ ◄─── SSE: tool_result ───────────│                      │                       │
   │ ◄─── SSE: agent_end (Pricing) ───│                      │                       │
   │                               │   │                     │   delegate_to_critic  │
   │                               │   │                     │   ┌─────▶ Critic.run  │
   │                               │   │                     │   │   read_artifact   │
   │                               │   │                     │   │   query_sales     │
   │                               │   │                     │   │   wiki_search     │
   │                               │   │                     │   │   write_artifact  │ → artifact: critique
   │                               │   │                     │   │      (critique)   │
   │                               │   │                     │   └────────────────── │
   │                               │   │                     │   if Gaps/Risks:      │
   │                               │   │                     │     delegate_to_      │
   │                               │   │                     │       pricing(revise) │
   │                               │   │                     │     ... (loop) ...    │
   │                               │   │                     │   stage_chain_to_final│ → artifact stage flips
   │                               │   │                     │   Chief synthesises   │
   │ ◄─── SSE: text (Chief reply) ────│ ◄───────────────────┘                        │
   │                               │                         │                       │
   │                               │ post-turn:              │                       │
   │                               │   wiki auto-publish ────┼──────────────────────▶│ → wiki_publish (W4)
   │                               │   wiki curator ─────────┼──────────────────────▶│ → wiki_edit (W6)
   │                               │   brain ingest ─────────┼──────────────────────▶│ → brain_ingest
   │                               │   reset current_turn_id │                       │
   │                               │                         │                       │
   │                               │ /api/chat returns       │                       │
   │                               │                         │                       │
Browser polls /api/action-queue → ApprovalRail surfaces "promotion · pending"
Operator clicks Apply
                                  /api/integrations/erpnext/actions/<id>/apply
                                  → ERPNextAdapter.apply_outbound
                                  → POST /api/resource/Pricing Rule (draft)
                                  → outbox_actions.status = draft_created
                                  → external_id captured
```

Three things to notice:

1. **One operator turn → many spine events.** The audit rail is
   complete. `/api/events?since=<ts>` reconstructs the entire turn.
2. **Mesh review loop is invisible to the operator unless they look.**
   The cockpit's Reports tab defaults to `stage='final'` — drafts +
   critiques are there for debugging, hidden by default.
3. **External writes always land as drafts.** Even after Apply, the
   external system has a draft Pricing Rule. The operator opens
   ERPNext to ship it. Cockpit never auto-ships.

---

## 10. Track-by-track concerns

The architecture is the result of seven tracks layered on top of
each other. Each owns a slice of behaviour:

### Track 1 — Real-system rollout

Six adapters in `app/integrations/systems.py`. Each follows the
6-phase per-system rollout (P1 instance / P2 seed / P3 inbound /
P4 outbound / P5 UAT / P6 docs). Mock mode is the default; live
mode requires the system-specific env block.

### Track 2 — Agent mesh upgrade

The Critic agent + the draft → critique → revision loop in
`chief_of_staff._run_delegate_with_review`. Convergence regex:
`*No material findings.*` under both `## Gaps` and `## Risks`.
Max revision rounds capped at `MESH_MAX_REVISION_ROUNDS` (default 2);
mesh downgrade event fires on cap hit. Eval harness in
`tests/agents/eval/` scores single-pass vs multi-pass on 4 seeded
scenarios.

### Track 3 — Native app

`MobileShell.tsx` collapses the desktop layout into vertical sections.
`lib/pushNotifier.ts` emits OS notifications via Tauri (or
`window.Notification` in browser fallback) when a new approval lands.
Per-action dedup so one approval doesn't ping 12× per minute.

### Track 4 — MLflow + MLOps

`app/llm/tracing.py` wraps the LLM provider with MLflow nested-run
instrumentation. Each operator turn opens a parent run; every
delegate / critic / leaf-LLM call nests underneath. The `[MLFLOW]`
tab embeds the MLflow UI. Prompt registry (M4) lives at `prompts/`.
CI gate (M6) runs the eval harness on PRs touching prompts or agents.

### Track 5 — Agentic Wiki

`wiki_pages` + `wiki_revisions` tables, `app/spine/wiki.py` storage,
`build_wiki_*_tool` builders, the Wiki Curator post-turn agent, and
the W4 auto-publish gate inside `chief_of_staff._wiki_auto_publish_
clean_drafts`. Cockpit `[WIKI]` tab. Promotion is per-draft: each
edit must cite refs the Critic actually reviewed *this turn* AND the
edit's version must match the page's current version (atomic
`expected_version` check inside `publish_page`).

### Track 6 — GBrain integration

`app/llm/mcp.py` HTTP client. Brain tool builders. Ingest hook in
`run_chief`. Cockpit `[BRAIN]` tab. **Mock-by-default** so the demo
runs without a live `gbrain serve` instance. Track 5 vs Track 6
split documented in `docs/track6/...-reconciliation.md`: Wiki keeps
retail-domain pages, GBrain owns the operator's broader durable
memory + code-graph.

### Track 7 — DSPy + prompt optimization

`app/agents/dspy_signatures/` — Analyst + Pricing Signatures.
`app/agents/dspy_compile.py` — bridge from a compiled `dspy.Module`
to `prompts/<slug>/v<n+1>.md` (handwritten policy block preserved
verbatim, `## Few-shot demos` appended). `scripts/dspy_optimize.py`
drives BootstrapFewShot or MIPROv2. Cockpit's Reports tab DSPy
panel kicks off compiles via `/api/dspy/optimize/{slug}`. CI gate
(`prompt-alias-gate.yml`) blocks merge on `policy_adherence`
regression.

### Track 8 — Improvement Auditor

Operator-triggered audit loop. The cockpit's `[IMPROVE]` tab has an
`audit now` button that POSTs `/api/improvements/run`; a daemon
thread fires `app.agents.improvement_auditor.run_audit(run_id, llm)`
which:

1. Calls `gather_signals()` — pure-Python, deterministic. Walks
   substrate KPIs, flagged categories, inventory health, action-queue
   depth, store exceptions, integration mode, wiki coverage gaps,
   recent agent decisions. Returns one structured dict.
2. Builds an `Agent` with a single tool, `record_suggestion`. Closes
   over `run_id` so the agent doesn't re-pass it.
3. Drives the agent over the signals snapshot. The agent emits 3-8
   suggestions via the tool; each call validates `area` / `severity`
   / `action_hint` against an enum and writes a row to
   `improvement_suggestions`.
4. Flips `improvement_runs.status` to `ok` / `error` and appends an
   `observation` event to the spine.

The agent is **read-only** — it never proposes outbox actions, never
edits substrate, never publishes wiki pages. Operator decides what
to do with each suggestion via `accept` (status flips to `accepted`)
or `dismiss` (`dismissed`). Future integrations can pivot accepted
suggestions into outbox actions; the current scope stops at
operator review.

Schema: two new tables — `improvement_runs(id, started_ts, ended_ts,
status, summary_json, error)` and `improvement_suggestions(id, run_id,
area, severity, title, body_md, action_hint, status, refs_json, ts)`.

Status strip surfaces a `⚡ improve N` chip when N ≥ 1 open
suggestions exist — same chip pattern as the wiki-pinned and brain
chips. Hover shows the top 8 titles inline.

---

## 11. Concurrency model

Single-process uvicorn. FastAPI's threadpool runs sync handlers in
worker threads. `/api/chat` opens an SSE stream backed by a producer
thread that drives `run_chief` and pushes events into an
`asyncio.Queue`.

Concurrency hazards we've hit + mitigated:

| Hazard                                             | Where           | Fix                                                        |
|----------------------------------------------------|-----------------|------------------------------------------------------------|
| Two chats share wall-clock window for wiki publish | Track 5         | `current_turn_id` ContextVar + `events_for_turn`.          |
| `UNIQUE(slug, version)` race on `wiki_revisions`   | Track 5         | 3-attempt retry on `IntegrityError` in `propose_edit`.     |
| TOCTOU between version check + `publish_page`      | Track 5         | `expected_version` parameter; UPDATE includes version.     |
| Curator per-slug rate limit check-then-write       | Track 5         | Process-wide `threading.Lock` keyed by slug.               |
| Two DSPy compiles pick same `v<n+1>.md`            | Track 7         | `os.O_CREAT \| os.O_EXCL` atomic create + retry.           |
| Eval-harness `<AGENT>_PROMPT_ALIAS` env leaks      | Track 7         | `try/finally` snapshots + restores prior env.              |

---

## 12. What's intentionally stubbed

- Real production integrations to POS, OMS, WMS/3PL, CRM/CDP, EDI,
  ad networks, payment networks, finance.
- External-system write-back without explicit approval. Proposed
  writes go through `outbox_actions` first.
- Loop scheduler (cron-replaced by tool-driven measurement).
- Editable Policy & Spec Registry as a separate human surface.
  Currently embedded in agent SYSTEM prompts.
- Auth, multi-tenancy, RBAC.
- Persistent multi-tenant state. The single SQLite file is per-
  process, per-host.

Don't add these without an explicit ask.

---

## 13. Where to look when …

| You want to …                          | Read                                                  |
|----------------------------------------|-------------------------------------------------------|
| Add a tool to a specialist             | `app/agents/<name>.py` — TOOLS + IMPLS                |
| Add a new specialist                   | `app/agents/chief_of_staff.py::build_orchestrator`    |
| Change the schema                      | `app/spine/db.py::SCHEMA`                             |
| Add an integration adapter             | `app/integrations/systems.py` + `IntegrationAdapter`  |
| Change LLM provider behaviour          | `app/llm/{anthropic,openai,google}_p.py` (all three)  |
| Add a frontend tab                     | `frontend/src/components/dataTabs/` + `DataRail.tsx`  |
| Add a backend route                    | `backend/app/main.py`                                 |
| Bump an agent's prompt                 | `prompts/<slug>/v<n+1>.md` + `aliases.json`           |
| Change the review-loop convergence rule| `app/agents/chief_of_staff.py::_critique_is_clean`    |
| Tweak the wiki auto-publish gate       | `app/agents/chief_of_staff.py::_wiki_auto_publish_*`  |
| Change brain mock-mode behaviour       | `app/llm/mcp.py::GBrainClient._mock_*`                |
| Add a DSPy-compiled agent              | `dspy_signatures/<name>.py` + register in            |
|                                        | `scripts/dspy_optimize.py::_agent_registry`           |

---

## 14. Cross-layer invariants

These are the rules the codebase consistently follows. Breaking any
of them causes hard-to-trace bugs.

1. **The Chief of Staff is the only agent the operator chats with.**
   It must delegate at least one specialist before answering — even
   simple questions go to the Analyst.
2. **An `Agent` never imports another `Agent`.** Specialists are
   composed only inside `chief_of_staff.build_orchestrator`.
3. **External-system mutations always go through `propose_outbound`
   first.** They live in `outbox_actions` until `apply_outbound` is
   called.
4. **Every meaningful agent action appends to the event log.** User-
   visible reports persist as artifacts and reference the event so
   they show up in the audit rail.
5. **Tools call substrate helpers, not raw SQL.** This keeps the
   schema isolated and makes substrate-only refactors safe.
6. **Tests use `tempfile.TemporaryDirectory` + patch `db.DB_PATH`.**
   Don't mock SQLite — exercise the real schema.
7. **LLM provider changes touch all three implementations or none.**
   Agents are written once against `LLMProvider`; one-provider drift
   silently breaks the others.
8. **Mock mode is the default everywhere.** Track 1 adapters,
   Track 6 GBrain, Track 4 MLflow, Track 7 DSPy — every external
   dependency degrades gracefully when its env block is missing.
   The cockpit demo path runs from a fresh checkout with zero
   external services.
