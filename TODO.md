# TODO

Two parallel tracks. Pick whichever has the next freeing-up unit.

1. **Track 1 — Real-system rollout**: replace mock-mode adapters with real instances (ERPNext first, then Mautic / Medusa / OpenBoxes / Akeneo / Superset). Each system has its own 6-phase rollout (P1–P6).
2. **Track 2 — Agent mesh upgrade**: turn the single-shot delegate-and-write loop into a multi-agent dialogue with critique before the final report ships.

---

# Codebase cleanup audit - 2026-05-03

Context from this pass: the repo is now a full vertical-slice workbench, not just the original mock demo. The runtime is FastAPI in `backend/app/main.py`, a specialist agent mesh in `backend/app/agents/`, a SQLite spine in `backend/app/spine/` plus `backend/data/spine.db`, mocked retail substrate helpers in `backend/app/substrate/`, live/mock system adapters in `backend/app/integrations/`, and a React/Vite cockpit in `frontend/src/`.

Audit evidence:
- [x] Frontend build passes: `cd frontend && npm run build`.
- [x] Backend test suite is fully green by default. Live integration suites are now opt-in via `RUN_LIVE_TESTS=1` / `RUN_<SYSTEM>_LIVE=1` (gate in `backend/tests/_live_gate.py`); `make test` is mock-only and ignores `.env` creds. Live runs go through `make test-live` / `make test-live-<system>`. The previously failing `ERPNextLiveApplyTest.test_po_held_annotates_existing_purchase_order` no longer runs unattended — it now requires `RUN_ERPNEXT_LIVE=1` plus a freshly seeded ERPNext.

## Highest leverage cleanup

- [x] **P0 - Stabilize live integration tests.** Done — every `test_integrations_<system>_live.py` is now opt-in via the shared gate at `backend/tests/_live_gate.py`. Default `make test` is mock-only and stays green even when `.env` carries live creds. `make test-live` flips `RUN_LIVE_TESTS=1`; per-system targets (`make test-live-erpnext`, …, `make test-live-shopify`) flip exactly one flag, so a brittle adapter (e.g. ERPNext PO-hold drift) can never break the default suite. The "reseed preconditions / repair local stack" half is still open and is now folded into adapter-specific cleanup work.
- [x] **P0 - Split `backend/app/integrations/systems.py` into adapter modules.** Done — the 3,357-line monolith split into seven per-adapter modules under `backend/app/integrations/adapters/{akeneo,erpnext,mautic,medusa,openboxes,shopify,superset}.py` (260–760 lines each), with `JsonHttpClient` + shared helpers (`_slug`, `_display`, `_safe_float`, `_safe_int`, `_coerce_id`) lifted to a new `http.py`. `adapters/__init__.py` owns the canonical `ADAPTERS` list; `registry.py` imports from there. The original `systems.py` is now a 53-line shim re-exporting every name so existing test/agent imports keep working — new code should reach for the per-adapter modules directly.
- [x] **P0 - Extract route modules from `backend/app/main.py`.** Done — the 765-line `main.py` collapsed to 56 lines (app setup + CORS + startup hook + `include_router` for each domain). Endpoints split across `routes/{core,integrations,mesh,wiki,mlflow,improvements,brain,dspy,chat}.py` (40–138 lines each). The DSPy in-process job dict + lock moved to `routes/dspy.py`; the one test that monkey-patched `app.main._run_dspy_compile_job` now patches `app.routes.dspy._run_dspy_compile_job` instead.
- [x] **P0 - Add a one-command quality gate.** Done — `make check` runs `ruff` (lint) + `pyright` (types) + `make test` (mock-only) + `npm run typecheck` + `npm run test` + `npm run build` (frontend). Sub-targets `make lint`, `make typecheck`, `make frontend-check` cover individual stages. `.github/workflows/check.yml` wires the same gate on PRs and pushes to `main`.
- [x] **P0 - Introduce Python lint and typing checks.** Done — `ruff` and `pyright` configured in `backend/pyproject.toml` under `[project.optional-dependencies] dev`. Ruff runs the F/E/B/UP/I rule families (line length ignored on purpose). Pyright runs in non-invasive basic mode with the noisiest categories (Optional* access, ArgumentType, ReturnType) silenced; tighten module-by-module after the `systems.py` / `main.py` splits. Real bugs surfaced and fixed in this pass: two missing `raise ... from e` on `HTTPException` reraises (`app/main.py`), a dead `last_err` retry-loop in `app/spine/wiki.py`, and a few unused locals.

## Backend correctness and architecture

- [x] **P1 - Create a real schema migration path for the SQLite spine.** Done — new `backend/app/spine/migrations.py` owns an append-only `MIGRATIONS` list keyed by version. v1 is the baseline (the SCHEMA that used to live inline in `db.py`); future migrations append `(version, description, sql)` tuples and never edit prior entries. `init_db()` tracks applied versions in a new `schema_version` table and only runs pending migrations. Pre-migrations DBs are adopted cleanly via `IF NOT EXISTS` semantics (no destructive reseed). New `tests/test_migrations.py` covers fresh init, adoption of pre-migrations DBs, idempotent re-init, and dense-ascending version numbering.
- [x] **P1 - Make event kinds a shared contract.** Done — `EVENT_KINDS` frozenset declared in `backend/app/spine/events.py`; `append_event` raises `ValueError` on any kind outside the set. Audit found 13 distinct kinds in actual use (the architecture-doc canon plus `mesh_downgrade`, `wiki_*`, `brain_ingest`, `campaign_launch`); strings like `critique` / `report` / `*_plan` were artifact kinds, not event kinds, so they don't enter the set. New `tests/test_event_kinds.py` round-trips every allowed kind and confirms unknowns reject.
- [x] **P1 - Decide what to do with chat history.** Done — removed. `ChatRequest.history` was a dead field; `/api/chat` only passes `req.message` into `run_chief`, and the frontend keeps prompt history locally as terminal-style up-arrow recall. Field deleted from `app/schemas.py`; nothing in the wider codebase depended on it.
- [x] **P1 - Durable job tracking for background work.** Done — new v2 migration adds `background_jobs(id, kind, title, status, started_at, ended_at, metadata_json, summary_json, error)`. Helper module `app/spine/jobs.py` exposes `create_job` / `complete_job` / `cancel_job` / `running_count`. DSPy compile route migrated off the in-process `_DSPY_JOBS` dict — terminal status now survives backend restart. Improvement auditor already lived in SQLite (`improvement_runs`); brain ingest is intentionally fire-and-forget. Concurrency cap is exposed via `running_count(kind)` for the caller to gate; not enforced globally yet.
- [x] **P1 - Normalize integration apply semantics.** Done — lifted the identical 45-line `apply_outbound` shell out of every adapter into `IntegrationAdapter` base. Subclasses now declare just `LIVE_ACTION_TYPES` (set) and implement `_dispatch_outbound`. The base shell handles fetch / idempotency / mock pass-through / error landing / draft_created result shaping uniformly. Removed ~270 lines of duplicated boilerplate across 6 adapters.
- [x] **P1 - Harden outbound matching against real-system drift.** Done — ERPNext PO match path now tries `external_refs` lookup first (keyed by substrate `po_id`), falls back to vendor + schedule_date filter only when the external_ref is missing. Match strategy is recorded per PO in the apply result `details.match_strategy` so the cockpit drawer can surface which path matched. Unmatched rows now carry an explicit remediation hint ("re-sync ERPNext to refresh external_refs and retry").
- [x] **P1 - Add API response models for mutable routes.** Done — `app/schemas.py` now defines `_Lenient` Pydantic V2 base + per-domain models: `IntegrationSystemsResponse`, `SyncRunsResponse`, `OutboxAction`, `ImprovementRunsResponse`, `ImprovementSuggestionsResponse`, `WikiPagesResponse`, `BrainStatus`, `DspyAgentsResponse`, `DspyJobsResponse`, `MeshStatus`, etc. Routes wired through `response_model=...` for the read paths (`/api/integrations/systems`, `/api/integrations/sync-runs`, `/api/improvements/runs`, `/api/improvements/suggestions`, `/api/wiki/pinned`, `/api/dspy/agents`, `/api/dspy/jobs`, `/api/mesh/status`, `/api/brain/status`). `_Lenient` ignores extra keys so adapter-specific fields don't trip validation.
- [x] **P1 - Scope CORS for non-demo runs.** Done — `app/main.py` now reads `CORS_ORIGINS` (comma-separated) and falls back to `["*"]` for the demo default. `backend/.env.example` documents the env var. Tests pin the parser against trailing commas / whitespace so a typo doesn't admit an empty origin (which can behave like `*` in some middleware versions).

## Frontend cleanup

- [x] **P1 - Centralize frontend fetch/error handling.** Done — new `apiFetch<T>()` + `ApiError` class in `frontend/src/lib/api.ts`. Non-2xx responses, network errors, and JSON parse failures all throw `ApiError` carrying `status` (0 for network/parse) + `url`. Existing direct `fetch(...).json()` calls left in place for now (deep refactor) but new code paths must use `apiFetch`. Vitest tests in `src/__tests__/apiFetch.test.ts` pin the contract.
- [x] **P1 - Replace broad `Record<string, any>` types with domain types.** Done — introduced `JsonObject = { [key: string]: unknown }` in `api.ts` and replaced every `Record<string, any>` callsite. Cockpit now narrows `unknown` before reading instead of trusting `any`. Two read sites in `Chat.tsx` (SSE `tool_call` / `tool_result` payloads) updated with explicit casts since SSE shape varies by tool.
- [x] **P2 - Move tab metadata to a component registry.** Done — `DataRail.tsx` now drives both header buttons and panel rendering off a single `TABS: TabSpec[]` registry. Each entry owns id / label / `render(ctx)` function. Adding a tab = appending one entry. Header keyboard shortcuts (1-0) read the same list, so the three drift points collapse into one.
- [x] **P2 - Add focused UI tests for approval and error states.** Done — added `vitest@^2` + `npm run test` script + `frontend/src/__tests__/apiFetch.test.ts` covering the 4 paths (2xx success, non-2xx → ApiError with status, network error → ApiError(0), JSON parse failure → ApiError(0)). `make frontend-check` runs vitest before vite build, so the gate is wired. Drawer apply / stage-gated approvals / chat SSE rendering tests would need react-testing-library setup; folded into a follow-up note rather than landed here.

## Data, docs, and operations

- [x] **P1 - Add deterministic local reset commands.** Done — `make reset-demo` drops `spine.db`, re-runs the substrate seed (which now bootstraps via the migration ledger from PR #43), and prunes accumulated `artifacts/` to a configurable retention (`ARTIFACTS_RETAIN`, default 200). Live-system stacks intentionally not touched — their `*-nuke` targets handle that.
- [x] **P1 - Separate product backlog from cleanup backlog.** Done — shipped track history (Tracks 1-7) moved to `docs/roadmap/HISTORY.md`. `TODO.md` is now ~120 lines of active-queue: cleanup audit + Track 8 (Shopify/NetSuite/Dynamics) + Shopify App Store gate.
- [x] **P2 - Keep generated local assets observable but bounded.** Done — new `make artifacts-prune` target keeps the most-recent `ARTIFACTS_RETAIN` markdown files (default 200) and deletes the rest. Folded into `make reset-demo` for one-shot housekeeping.
- [x] **P2 - Add architecture fitness checks.** Done — new `tests/test_architecture.py` greps each layer's source for forbidden imports: `substrate` / `spine` / `integrations` / `llm` must not import from `agents` or `routes`; `agents` must not import from `routes`. Cheap regex test, runs in milliseconds, catches regressions PR review tends to wave through.
- [x] **P2 - Document a production-readiness boundary.** Done — new `docs/PRODUCTION-READINESS.md` lists what is intentionally not production-ready (auth, tenant isolation, secrets rotation, durable workers, rate limiting, external retry policy, audit retention, observability, multi-region, PII/GDPR, frontend auth-aware UX, backups), plus where each fix would land if work crosses the prototype → production boundary.

---

# Codebase review - 2026-07-03

Full-repo review pass (backend + frontend + docs). Items that duplicate an open
entry in the 2026-05-03 audit are NOT re-listed as checkboxes — new evidence for
those is folded into the "Reinforces existing open items" block at the end so
the backlog keeps one canonical checkbox per task.

## Reliability under concurrency

- [ ] **P0 - Enable WAL + busy_timeout on the SQLite spine.** `conn()` in `backend/app/spine/db.py:297-305` opens a fresh connection per call with no `journal_mode=WAL` and no `busy_timeout` — Python's sqlite3 default fails almost immediately on lock contention. Concurrent writers already exist: the chat producer runs in an executor thread (`routes/chat.py:50`), the improvement auditor spawns an uncapped daemon thread per `/run` (`routes/improvements.py:40`), brain-ingest spawns daemon threads (`agents/chief_of_staff.py:768`), and every sync route runs in FastAPI's threadpool. Two PRAGMAs in `conn()` buy most of the fix; add a regression test that hammers concurrent writers.
- [x] **P0 - Wire `make check` into CI.** Done — `.github/workflows/check.yml` (landed via PR #43) runs `make check` on PRs and pushes to `main`.
- [x] **P0 - Make CLAUDE.md true (or trim it).** Done — resolved by merging PR #43 (which shipped `spine/migrations.py`, `spine/jobs.py`, and vitest in `make frontend-check`); agent guidance now lives in `AGENTS.md` (`CLAUDE.md` imports it). Original finding: The current CLAUDE.md edit documents `spine/migrations.py` and `spine/jobs.py` in detail — neither exists (`spine/` has only db.py, events.py, artifacts.py, kg.py, wiki.py, telemetry.py; DSPy jobs still live in the in-memory dict per `routes/dspy.py:19-20`). It also says `make frontend-check` runs vitest — the target is tsc + vite build only, and the frontend has zero test files. Either ship those modules (they're the right design — see the two P1 items in the 2026-05-03 audit) or cut the doc back to reality before committing; a CLAUDE.md describing fictional modules misleads every future session.

## LLM layer resilience

- [ ] **P1 - Add retry/backoff to all three LLM providers.** Each provider makes a single blocking call (`llm/anthropic_p.py:49`, `llm/openai_p.py:91`, `llm/google_p.py:110`); one transient 429/500/overloaded aborts an entire multi-specialist Chief turn. Add a shared retry-with-backoff wrapper (per the "change all three impls or none" rule). No `tenacity`/`backoff`/hand-rolled retry exists anywhere in `app/`.
- [ ] **P1 - Fix Google provider drift.** `google_p.py` sets no `max_output_tokens` (anthropic/openai cap at 4096) and indexes `resp.candidates[0]` (`google_p.py:119,136`) — a safety-blocked/empty-candidate response yields silent empty text or IndexError. Bring it to parity with the other two impls.
- [ ] **P1 - Thread real token usage through `AssistantTurn`.** The mesh budget guard relies on `_approx_tokens` (`chief_of_staff.py:203-210`) = words × 1.3 of the specialist's *final text only* — no tool traffic, no intermediate turns, no critic rounds. It massively undercounts, making `turn_token_budget` nearly decorative. All three SDKs return usage; surface `input_tokens`/`output_tokens` on `AssistantTurn` and feed `_MeshState` real numbers.
- [ ] **P1 - Cancel the agent turn on SSE client disconnect.** `routes/chat.py` producer thread runs `run_chief` to completion even after the client drops — tokens keep burning for nobody. Add a cancellation flag (e.g. `threading.Event`) checked per iteration in `Agent.run`, set from the `finally` in `event_gen`.

## Streaming and cockpit responsiveness

- [ ] **P1 - Stream specialist events live instead of in a post-hoc burst.** Delegation runs synchronously inside the Chief's tool impl, so `_EventBuffer` only drains after the specialist *finishes* (`chief_of_staff.py:84-94`, drained in `run_chief` between CoS events) — during a 60s Pricing + Critic + revision loop the operator sees nothing, then everything. Restructure so the sink drains while the impl runs (queue + generator handoff, or generator-based tool impls).
- [ ] **P1 - Debounce the frontend refetch storm.** Every streamed agent event triggers a full 9-endpoint `Promise.all` refetch (`frontend/src/lib/data.tsx:74-88` via `bump()` at 119-121, called per event from `Chat.tsx:75`), stacked on `setInterval(refresh, 5000)` (data.tsx:115) plus six independent panel timers (EventTape 2s, StatusStrip 1s/5s, WikiTab/MlflowTab/BrainTab 5s, ImproveTab ~1.5s). Debounce/coalesce `bump`, and gate polling on document visibility / active streaming.
- [ ] **P2 - Cap chat memory + split the monolithic dashboard context.** `Chat.tsx` never caps `turns` and copies the whole array per event (`Chat.tsx:66,77,80` — O(n) per event, unbounded growth in long sessions); `traceEvents` flatMaps all turns every render (47-57). Separately, `DashboardProvider` holds all 10 data slices in one context object (`data.tsx:123-128`), so any poll re-renders every consumer. Cap/virtualize turns; split the context or add selectors.
- [ ] **P2 - Handle dropped SSE streams in the chat client.** `chatStream` (`api.ts:621-672`) treats a half-completed stream the same as a clean finish — `done` just flips `streaming` off with no retry/resume or user-visible "stream interrupted" state.

## Mesh design

- [ ] **P2 - Structured Critic verdicts instead of regex-over-markdown.** `_critique_is_clean` (`chief_of_staff.py:100-125`) parses `## Gaps`/`## Risks` headings and matches `*No material findings.*` to gate both the revision loop and wiki auto-publish — load-bearing safety logic hanging off prompt-formatting compliance. Have the Critic emit a machine-readable verdict (e.g. `findings: {gaps: N, risks: N}` in artifact frontmatter), keep the markdown for humans.
- [ ] **P2 - Split `chief_of_staff.py` (856 lines, three concerns).** Orchestrator build, the ~150-line wiki auto-publish policy (`_wiki_auto_publish_clean_drafts`), and brain ingest (`_fire_brain_ingest`) live in one module. Move the latter two into their own modules (e.g. `agents/_turn_postprocess.py` or under `spine/`); the auto-publish gate logic deserves its own focused tests.
- [ ] **P2 - Add a real `turn_id` column to events.** `events_for_turn` (`spine/events.py:74-117`) and `events_since_ts` (141-159) do full-table scans and JSON-parse every payload to match `turn_id` in Python — grows unbounded with the event log. Unblocked now that `spine/migrations.py` exists — land it as a new migration (`ALTER TABLE events ADD COLUMN turn_id` + index + backfill).
- [ ] **P2 - Make provider switching request-scoped.** `POST /api/config` mutates process-global `os.environ["LLM_PROVIDER"]` (`config.py:61`) — races with in-flight turns and permanently overrides the auto-pick logic for the life of the process. Move to app-state or per-request scoping. Related: `Settings` re-reads `os.getenv` on every property access (config.py:20-55).

## Integrations layer

- [ ] **P2 - Shared HTTP retry/timeout for adapters.** `JsonHttpClient.request` (`integrations/http.py:67-77`) is `urlopen(req, timeout=12)` — hardcoded timeout, no retry for transient 5xx (Medusa/OpenBoxes hand-roll a 401 re-login but nothing covers timeouts/5xx). `akeneo.py:78` bypasses the shared client entirely with its own `urlopen`. Add configurable timeout + bounded retry in one place; route Akeneo through it.

## Test gaps

- [ ] **P2 - Add error-path coverage.** The ~7k-line suite is 100% happy-path: zero 4xx/5xx status assertions, zero exception-path tests. Start with the routes that remap `{"error"}` dicts to HTTPException.
- [ ] **P2 - Cover the untested modules.** Zero tests reference: all three LLM providers, `config.py` (`Settings`/`set_provider`), `integrations/http.py` (JsonHttpClient), and the chat SSE route (`routes/chat.py` has no TestClient exercise). The stop-reason classifier and stuck-loop guard in `agents/base.py` have tests; the providers feeding them don't.
- [ ] **P2 - Bootstrap frontend component tests.** Partially done — vitest + `src/__tests__/apiFetch.test.ts` landed via PR #43. Still missing: Testing Library + component tests. Original finding: zero test files; no vitest/testing-library in package.json — yet components already carry `data-testid` hooks (`Chat.tsx:130,138,175,211`). Add vitest + Testing Library starting with Chat's SSE handling. (Overlaps the 2026-05-03 "focused UI tests" item — this is the missing tooling prerequisite.)

## Housekeeping

- [ ] **P2 - Decide where `startup-pack/` lives.** Business docs (pitch deck, financial model, incorporation, legal) sitting untracked in the code repo — commit deliberately, move to another repo, or `.gitignore` before it lands in a commit by accident.

## Reinforces existing 2026-05-03 audit items (no new checkboxes)

> Status after merging PR #43: migrations, durable jobs, apply-semantics normalization and response models are **done**; the evidence below is kept for the parts that are still open (uncapped improvement-auditor thread, legacy unguarded `fetch` calls, raw `dict` request bodies).

- **Schema migrations (P1, open):** new evidence — CLAUDE.md already documents the intended `migrations.py` design (append-only `(version, description, sql)` tuples + `schema_version`); the `turn_id` column item above is blocked on it.
- **Durable job tracking (P1, open):** new evidence — `routes/improvements.py:40` starts an uncapped daemon `Thread` per `/run` call; combined with no SQLite busy_timeout this is a live lock-contention source. CLAUDE.md already documents the intended `spine/jobs.py` contract.
- **Normalize integration apply semantics (P1, open):** new evidence — `apply_outbound` is ~50 near-identical lines in six adapters (`erpnext.py:291-355`, `medusa.py:306-362`, `shopify.py:417-471`, plus akeneo/mautic/openboxes); `_cache`/`_payload_marker`/the `sync_inbound` try-except guard repeat in all seven (`_payload_marker` byte-identical in shopify.py:485-491 vs medusa.py:374-385). Template method in `base.py`; adapters keep only `_dispatch_outbound` + action types. Note these live paths only run under `RUN_LIVE_TESTS=1`, so the duplication is effectively unexercised in CI.
- **Centralize frontend fetch/error handling (P1, open):** new evidence — 38 `fetch` calls, only 17 `r.ok` guards; the older core endpoints (`getConfig` api.ts:208, `listEvents` 222, `getKpis` 241, …) call `r.json()` unguarded while newer blocks (468-617) check — split-brained error handling. Consider generating types from FastAPI's OpenAPI schema (openapi-typescript) instead of the ~50 hand-redeclared mirrors.
- **API response models (P1, open):** extend scope to *request* bodies too — `wiki.py:50,59,70` and `integrations.py:70` take raw `dict` bodies read via `.get()`; the Mautic webhook ingests an unvalidated external dict straight into `cache_record`.

---

Shipped track history (tracks 1-7) moved to [`docs/roadmap/HISTORY.md`](docs/roadmap/HISTORY.md). This file keeps the active queue + active rollouts.

# Track 8 — Distribution adapters

Goal: extend the integration-adapter surface to platforms used by larger mid-market retailers — Shopify Plus, NetSuite, Microsoft Dynamics 365 Commerce. Each follows the Track 1 P1–P6 rollout. Shopify Plus first (covers ~10× the retailer count of the existing six open-source adapters combined); NetSuite + Dynamics gated on a real customer pulling them.

Order:
1. **Shopify Plus** ← active
2. NetSuite (gated)
3. Microsoft Dynamics 365 Commerce (gated)

Per-system phases identical to Track 1: P1 local instance · P2 demo seed · P3 inbound sync · P4 live outbound apply · P5 agent-loop UAT · P6 docs + env-gated tests.

---

## Shopify Plus (active)

- [x] **P1 · Sandbox instance**
  - Shopify Partner dev store (free; Shopify-hosted, no Docker / compose).
  - `infra/shopify/README.md` — Partner account creation, dev store provisioning, custom-app install with Admin API access scopes (`read_products`, `write_products`, `read_orders`, `write_orders`, `read_inventory`, `write_inventory`, `read_locations`, `write_discounts`, `write_marketing_events`), env block for `SHOPIFY_SHOP_DOMAIN` / `SHOPIFY_ADMIN_TOKEN` / `SHOPIFY_API_VERSION`, reset path (uninstall app + reinstall to rotate creds).
  - No Makefile lifecycle targets — no local container to manage.

- [x] **P2 · Demo seed**
  - `infra/shopify/seed.py` — Admin GraphQL + REST, idempotent, stdlib-only (urllib + sqlite3).
  - Reads `backend/data/spine.db`. Creates: 30 Products (one per `substrate_skus`), 5 Locations (one per `substrate_stores`), inventory levels per (variant × location), 10 draft Orders.
  - Idempotency via `metafield.namespace="retail_os"` markers (`spine_sku`, `store_id`) so re-runs print zero `++` lines and round-trip back to substrate ids in P3.
  - Out of scope: Customers, Collections, multi-currency.
  - Makefile target: `shopify-seed` (no `up/down/bootstrap` — Shopify-hosted).

- [x] **P3 · Inbound sync**
  - `backend/app/integrations/systems.py` — extend the existing `ShopifyAdapter` (currently mock-only). `configured()` requires `SHOPIFY_SHOP_DOMAIN` + `SHOPIFY_ADMIN_TOKEN`.
  - `_live_sync()` pulls Products, Variants, Locations, InventoryLevels, Orders via Admin GraphQL with cursor pagination (uncapped by default; explicit `max_rows` surfaces `truncated=True` + `status="partial"` matching Mautic / Medusa pattern).
  - Each row caches into `record_cache`; local_id round-trips via `metafields.retail_os.spine_sku` (products) and `metafields.retail_os.store_id` (locations).
  - `external_url` deep-links: `admin/products/<id>`, `admin/orders/<id>`, `admin/settings/locations/<id>`, `admin/discounts/<id>`.
  - `sync_inbound()` dispatches: configured → `_live_sync`, else → existing `_mock_sync`.
  - Tests: extend `tests/test_integrations.py` with mock-mode unit tests; new `backend/tests/test_integrations_shopify_live.py` env-gated (live sync round-trip, seeded metafield external_ref round-trip, registry reports `mode=connected`).

- [x] **P4 · Live outbound apply**
  - `LIVE_ACTION_TYPES = {"promotion", "fulfillment_routing", "campaign_brief"}`. Everything else falls back to base mock-apply (matches the ERPNext / Mautic / Medusa shape).
  - `_shopify_create_discount` (promotion) → `discountAutomaticBasicCreate` GraphQL mutation with percentage off, 30-day window, target collection derived from the proposal's category. `automaticDiscountId` mirrors into outbox `external_id`.
  - `_shopify_route_fulfillment` (fulfillment_routing) → `fulfillmentOrderMove` to the recommended Location. Requires the order id in the payload.
  - `_shopify_campaign_brief` (campaign_brief) → `marketingActivityCreate` draft (Shopify Email) with `[retail-os:<campaign_id>]` marker in the description for round-trip; if `KLAVIYO_API_KEY` is set, override to POST `/api/campaigns` against Klaviyo instead. Either path lands a draft, never a sent campaign.
  - Adapter rejection lands the row in `error` (not `draft_created`) so the cockpit's red chip surfaces it.
  - Five new unit tests in `tests/test_integrations.py` (stub `_client`): promotion happy path, fulfillment-routing happy path, campaign-brief Shopify-Email path, campaign-brief Klaviyo passthrough path, unsupported-type fallback. Two new env-gated live tests in `tests/test_integrations_shopify_live.py`.

- [x] **P5 · Agent-loop UAT**
  - `docs/uat/<date>-shopify-p5-promo-demo.md` — full walkthrough: Pricing & Promo proposes → cockpit drawer apply → real automatic discount lands in dev store; idempotency note (re-applying the same outbox row → existing discount id reused, not duplicated); acceptance checklist; screenshot placeholders.
  - One Marketing demo path (`campaign_brief` → draft Shopify Email campaign) bundled in the same doc.

- [x] **P6 · Docs + tests**
  - README "Running with real Shopify Plus" section: full quick-start (Partner setup → dev store → custom-app install → env wiring → seed → sanity curl), per-action-type mapping table, troubleshooting cheat sheet (Admin API throttle, GraphQL cost-limit budgeting, custom-app scope drift, metafield-marker round-trip mismatch, Klaviyo opt-in), live-test instructions, reset path.
  - Live-path tests already env-gated (P3 + P4); README points at them so CI stays mock-only by design.

---

## NetSuite (gated)

- [ ] **G0 · Trigger** — block until ≥1 NetSuite-running design partner appears. Path: SuiteApp via SDF (SuiteCloud Development Framework); multi-month effort vs the weeks-scale of the open-source adapters. Until then: expensive optionality.
- [ ] P1–P6 — same `IntegrationAdapter` contract as Track 1. P3/P4 use the SuiteTalk REST + Restlet surface.

## Microsoft Dynamics 365 Commerce (gated)

- [ ] **G0 · Trigger** — block until ≥1 D365 customer pulls it. Heavy integration + slow Microsoft cert process.
- [ ] P1–P6 — same adapter contract; P3/P4 use the Commerce Scale Unit APIs.

---

## Shopify App Store listing (post-PMF)

- [ ] **L1 · Compliance audit** — security review (token storage, scope minimisation, webhook HMAC verification), support SLA documentation, performance benchmarks against Shopify's published thresholds (~4–8 weeks of compliance work). Defer until Shopify P1–P6 ship and ≥1 customer is live so distribution doesn't outrun product fit.
- [ ] **L2 · Listing submission** — public listing in the Shopify App Store, OAuth install flow, billing API integration for managed billing. Out of scope until L1 closes.
