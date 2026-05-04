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
