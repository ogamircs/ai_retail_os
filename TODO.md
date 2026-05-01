# TODO

Two parallel tracks. Pick whichever has the next freeing-up unit.

1. **Track 1 — Real-system rollout**: replace mock-mode adapters with real instances (ERPNext first, then Mautic / Medusa / OpenBoxes / Akeneo / Superset). Each system has its own 6-phase rollout (P1–P6).
2. **Track 2 — Agent mesh upgrade**: turn the single-shot delegate-and-write loop into a multi-agent dialogue with critique before the final report ships.

---

# Track 1 — Real-system rollout

Goal: replace mock-mode adapters with real systems, one at a time, in a foundation-first order. Each system goes through its own 6-phase rollout (P1–P6). When all 6 are done for a system, we move on.

**Strategy:** A · Foundation-first.

Order:
1. **ERPNext** ← active
2. Mautic
3. Medusa
4. OpenBoxes
5. Akeneo
6. Superset

For every system, the per-system phases are the same:

| Phase | Outcome |
|---|---|
| P1 · Local instance | Docker stack up, healthy, admin reachable |
| P2 · Demo seed | Idempotent script populates the system with retail data that mirrors our spine |
| P3 · Inbound sync | `.env` wired, `/api/integrations/{system}/sync` pulls real records into `record_cache` + `external_refs` (+ updates substrate where the adapter does so) |
| P4 · Live outbound apply | Adapter overrides `apply_outbound` to actually create real (draft) records on apply |
| P5 · Agent-loop UAT | One README demo path runs end-to-end against the real system |
| P6 · Docs + env-gated tests | README section + troubleshooting + skip-if-no-creds tests |

---

## ERPNext (active)

- [x] **P1 · Local instance** _(merged: `feature/erpnext-integration` → main)_
  - [x] `infra/erpnext/docker-compose.yml` (frappe/erpnext v15 multi-arch digest + mariadb 10.6 + redis 6.2)
  - [x] Root `Makefile` targets: `erpnext-up`, `erpnext-down`, `erpnext-bootstrap`, `erpnext-logs`, `erpnext-status`, `erpnext-nuke`
  - [x] `infra/erpnext/bootstrap.sh` creates site `retail.localhost`, installs erpnext app, sets admin pw, generates API key/secret idempotently
  - [x] `infra/erpnext/README.md` documents URL + creds + reset path + Apple Silicon digest pin rationale
  - **Verified:** `curl /api/method/frappe.auth.get_logged_user` → `{"message":"Administrator"}`

- [x] **P2 · Demo seed** _(merged: `feature/erpnext-p2-seed` → main)_
  - [x] `infra/erpnext/seed.py` — REST-based, idempotent. Creates Company `AI Retail OS` (abbr `ARO`), 3 Item Groups, 30 Items, 5 Warehouses (one per store), Suppliers, Brands, Customers, 5 opening Stock Entries (Material Receipt, submitted), 12 Purchase Orders, 10 Sales Invoices (drafts).
  - [x] Reads from `backend/data/spine.db` directly so the seed stays in sync with the canonical retail demo data.
  - [x] Idempotent across all entity types: re-runs print zero `++` lines.
  - [x] Makefile target `make erpnext-seed`.
  - **Verified:** ERPNext desk populated; second run is a no-op.

- [x] **P3 · Inbound sync** _(merged: `feature/erpnext-p3-sync` → main)_
  - [x] `backend/.env` wired with ERPNext API credentials
  - [x] `POST /api/integrations/erpnext/sync` succeeds against real ERPNext: 272 records across Item Group / Item / Warehouse / Bin / Customer / Supplier / Purchase Order
  - [x] `record_cache` + `external_refs` populate end-to-end; cockpit Integrations row shows `mode=connected` · last sync timestamp updated
  - [x] **Bin aggregation fix** — adapter previously overwrote `substrate_inventory.on_hand` with whichever Bin came last; now sums across all warehouses per SKU. Pinned by a regression test.
  - [x] `backend/tests/test_integrations_erpnext_live.py` — env-gated, three tests covering live sync, external_refs round-trip, and the Bin-aggregation fix. Skipped automatically when credentials aren't present so CI stays mock-only.

- [x] **P4 · Live outbound apply** _(merged: `feature/erpnext-p4-apply` → main)_
  - [x] Override `ERPNextAdapter.apply_outbound` — full real-mode dispatcher with per-action helpers and a base-class fallback for unsupported types.
    - [x] `promotion` → POST `/api/resource/Pricing Rule` with `apply_on=Item Group`, `Discount Percentage` rate, 30-day validity window.
    - [x] `po_held` → matches Purchase Orders by `(supplier, schedule_date)` and posts a `Comment` referencing the PO with the operator's reason.
    - [x] `po_expedited` → comment + best-effort `schedule_date` bumped 3 days earlier (silent fallback if PO is submitted and needs an Amend flow).
    - [x] `store_transfer` → POST `/api/resource/Stock Entry` (Material Transfer, draft) with the top-on-hand SKU as the line item.
    - [x] `fulfillment_routing` → POST `/api/resource/Sales Order` (draft, Walk-In customer, qty 1) with the recommended strategy in the result details.
  - [x] Returned doc `name` is stored as `external_id` in `outbox_actions`; failures land the row in `error` state instead of crashing the apply call.
  - [x] Three new env-gated tests in `tests/test_integrations_erpnext_live.py` covering Pricing Rule round-trip, PO annotation, and the unsupported-type fallback.

- [x] **P5 · Agent-loop UAT** _(merged: `feature/erpnext-p5-uat-on-p4` → main)_
  - [x] README markdown demo runs end-to-end against real ERPNext: Pricing & Promo proposes → cockpit drawer apply → real Pricing Rule lands in ERPNext (`PRLE-0005`, 25% off Summer Apparel, 30-day validity).
  - [x] UAT log + 5 screenshots in `docs/uat/2026-04-30-erpnext-p5-markdown-demo.md`.
  - [x] Frontend rail-filter bug surfaced + fixed: promotions with configured adapters now show up in the Pending rail (and the status-strip `APPROVE n!` chip) by sharing a `PENDING_EXTERNAL_STATUSES` set across `ApprovalRail.tsx` and `StatusStrip.tsx`.

- [x] **P6 · Docs + tests** _(merged: `feature/erpnext-p6-docs-on-p5` → main)_
  - [x] README "Running with real ERPNext" section: full quick-start (compose / bootstrap / env / seed / sanity curl), per-action-type mapping table, troubleshooting cheat sheet, live-test instructions, and reset path.
  - [x] Live-path tests already env-gated and skipped without creds (covered in P3 + P4); README now points at them so CI stays mock-only by design.

---

## Mautic

- [x] **P1 · Local instance** _(merged: `feature/mautic-p1-instance` → main)_
  - [x] `infra/mautic/docker-compose.yml` (mautic 5-apache multi-arch digest + mariadb 10.6 + dedicated cron + worker services)
  - [x] Root `Makefile` targets: `mautic-up`, `mautic-down`, `mautic-bootstrap`, `mautic-logs`, `mautic-status`, `mautic-nuke`
  - [x] `infra/mautic/bootstrap.sh` runs `bin/console mautic:install` once (idempotent — skips if `config/local.php` exists), enables `api_enabled` + `api_enable_basic_auth`, prints admin URL + creds + the `MAUTIC_BASE_URL` / `MAUTIC_USERNAME` / `MAUTIC_PASSWORD` env block to paste into `backend/.env`
  - [x] `infra/mautic/README.md` documents URL + admin creds + reset path + Apple Silicon digest pin rationale + the API endpoints the cockpit will hit in P3+

- [x] **P2 · Demo seed** _(merged: `feature/mautic-p2-seed` → main)_
  - [x] `infra/mautic/seed.py` — REST-based, idempotent. Reads `backend/data/spine.db` and projects: 4 Segments (one per `substrate_customer_segments`, with alias derived from segment id), 20 Contacts (5 stable personas × 4 segments, tagged with `seg-id` + `tier-<tier>` + `retail-os-seed`), N Campaigns (one *draft* per `substrate_campaigns` row — left unpublished because Mautic campaigns need actions before they can run; the campaign description embeds a `[retail-os:<id>]` marker so search-by-description matches the spine row on re-runs).
  - [x] Idempotent across all entity types: alias-based lookup for segments, email-based for contacts, marker-based for campaigns. Uses Mautic's column-scoped `where[]` filter syntax (uniform across endpoints) so the lookups don't drift between Lists / Contacts / Campaigns. Re-runs print zero `++` lines.
  - [x] Makefile target `make mautic-seed` (next to `mautic-bootstrap`).
  - [x] No-deps: stdlib only (urllib + base64 + sqlite3) so the script runs against the system python without needing `pip install` in the project venv.

- [x] **P3 · Inbound sync** _(merged: `feature/mautic-p3-sync` → main)_
  - [x] `MauticAdapter.configured()` overridden to require `MAUTIC_BASE_URL` *plus* either `MAUTIC_BASIC_TOKEN` or the `MAUTIC_USERNAME` / `MAUTIC_PASSWORD` pair — no auth means there's nothing real to talk to.
  - [x] `MauticAdapter._client()` factored out — single source of truth for basic-auth header resolution; `healthcheck` and the new live sync both go through it.
  - [x] `MauticAdapter._live_sync()` pulls Segments, Contacts, Campaigns from the real Mautic via `/api/segments`, `/api/contacts?limit=500`, `/api/campaigns`. Each row caches into `record_cache`, and where the seed left a recoverable spine-side id (segment alias `_` → `-`, contact email, campaign `[retail-os:<id>]` description marker) we also write an `external_refs` row so the cockpit's drawer can drill from the Mautic list id back to the substrate.
  - [x] `_mautic_list` paginates uncapped by default (walks `start` until Mautic's `total` is consumed, with a short-page fallback when `total` isn't returned); a caller that does pass `max_rows` and hits the cap gets `truncated=True`, surfaced via `summary["truncated_domains"]` and a `status="partial"` so an incomplete cache is never silent.
  - [x] `_coerce_id` validates Mautic row ids before stringifying — rejects None/blank/bool so malformed rows don't collide on `"None"` in `record_cache`.
  - [x] `external_url` deep-links: `s/segments/view/<id>`, `s/contacts/view/<id>`, `s/campaigns/view/<id>`.
  - [x] `sync_inbound()` dispatches: configured → `_live_sync`, else → `_mock_sync` (the prior behaviour, unchanged so substrate-only demos keep working).
  - [x] `backend/tests/test_integrations_mautic_live.py` — env-gated. Three tests: live sync round-trip, seeded-alias external_ref round-trip, registry reports `mode=connected`. Skipped automatically when creds aren't set so CI stays mock-only.

- [x] **P4 · Live outbound apply** _(merged: `feature/mautic-p4-apply` → main)_
  - [x] `MauticAdapter.LIVE_ACTION_TYPES = {"campaign_launch", "campaign_brief"}` — every other action_type falls back to the base mock-apply behaviour, same shape as ERPNext.
  - [x] `apply_outbound` dispatches: configured + supported type → `_dispatch_outbound`; else → `super().apply_outbound`. Adapter rejection lands the row in `error` instead of `draft_created` so the cockpit's red chip surfaces it; outbox `external_id` mirrors the Mautic doc id.
  - [x] `_mautic_create_campaign` (campaign_launch) → POST `/api/campaigns/new` with `name=title`, `isPublished=false`, and a description that embeds `[retail-os:<campaign_id>]` so the next sync round-trips back to the same substrate row via the existing P3 marker parser.
  - [x] `_mautic_ensure_segment` (campaign_brief) → idempotent. Looks up by `alias` (eq-filter) first; only POSTs `/api/segments/new` if not present. Re-applying a brief twice does not duplicate the list.
  - [x] Helpers: `_post(endpoint, body, key)` (factored POST + Mautic-shape unwrap) and `_mautic_find_one(endpoint, key, filters)` (mirrors the seed's column-scoped `where[]` lookup).
  - [x] Four new unit tests in `tests/test_integrations.py` (stub `_client`): campaign-launch happy path, brief-segment reuse, unsupported-type fallback, missing-payload → error landing.
  - [x] Two new env-gated live tests in `tests/test_integrations_mautic_live.py` (`MauticLiveApplyTest`): real campaign_launch creates a Mautic draft; campaign_brief reuses the seeded segment instead of duplicating.

- [ ] **P5 · Agent-loop UAT** _(operator-driven — needs live Mautic + cockpit + screenshots)_
  - Suggested demo: cockpit chat "draft a heatwave campaign for summer apparel" → Marketing agent proposes `campaign_brief` + `campaign_launch` → drawer apply → real Mautic Segment (already seeded as `seg_vacation`) reused + new draft Campaign with `[retail-os:<id>]` marker visible at `/s/campaigns`.
  - Output should land alongside the ERPNext walkthrough at `docs/uat/<date>-mautic-p5-campaign-demo.md` with screenshots of: Marketing draft, drawer apply, Mautic UI showing the new Campaign + reused Segment, second cockpit sync surfacing the Mautic id back as the substrate `local_id`.

- [x] **P6 · Docs + tests** _(branch: `feature/mautic-p6-docs` — open PR pending)_
  - [x] README "Running with real Mautic" section: full quick-start (compose / bootstrap / env / seed / sanity curl), per-action-type mapping table (`campaign_launch` → draft Campaign, `campaign_brief` → idempotent Segment, `campaign_measurement` → mock-apply), troubleshooting cheat sheet (auth drift, partial-sync truncation, alias round-trip mismatch, Apple Silicon digest pin), live-test instructions, and reset path.
  - [x] Live-path tests already env-gated (covered in P3 + P4); README now points at them so CI stays mock-only by design.

## Medusa

- [x] **P1 · Local instance** _(merged: `feature/medusa-p1-instance` → main)_
  - [x] `infra/medusa/Dockerfile` — minimal `node:22-alpine` image that clones `medusajs/medusa-starter-default` shallowly + `yarn install --frozen-lockfile` + `npx medusa build` (so `medusa start` finds the prebuilt admin bundle and doesn't restart-loop). Build args (`MEDUSA_STARTER_REPO`, `MEDUSA_STARTER_REF`) for tracking a fork. Medusa doesn't ship an official Docker image, so this is the upstream-recommended pattern.
  - [x] `infra/medusa/docker-compose.yml` — postgres:15-alpine + redis:7-alpine + the custom medusa service on :9000. Compose builds the image inline; volumes for db data, redis data, and `/app/uploads` so operator-uploaded media survives restarts.
  - [x] `infra/medusa/bootstrap.sh` — runs `npx medusa db:migrate` (idempotent) and `npx medusa user --email --password`. Captures user-creation output + exit code so a real failure (DB down, auth misconfig, password policy) aborts the bootstrap rather than printing creds that don't work; only the explicit "already exists / duplicate key / unique constraint / email is already" patterns are swallowed. Same `compose exec -e` pattern as Mautic so admin password apostrophes don't break the inner shell.
  - [x] `infra/medusa/README.md` — quick-start, image build rationale, lifecycle table, the `/admin/*` endpoints the cockpit will hit in P3+, troubleshooting cheat sheet, reset path.
  - [x] Root `Makefile` targets: `medusa-up`, `medusa-down`, `medusa-bootstrap`, `medusa-logs`, `medusa-status`, `medusa-nuke` (mirrors the erpnext/mautic patterns; `medusa-up` does `up -d --build` since the image is local).

- [x] **P2 · Demo seed** _(merged: `feature/medusa-p2-seed` → main)_
  - [x] `infra/medusa/seed.py` — REST-based, idempotent. Stdlib-only (urllib + sqlite3) so it runs against the system python with no `pip install`.
  - [x] Auth: `POST /auth/user/emailpass` for the v2 admin token, then `Authorization: Bearer <token>` for `/admin/*`.
  - [x] Creates: 1 Sales Channel ("Retail Demo"), 5 Stock Locations (one per `substrate_stores` row, store_id stashed in metadata for round-trip), 30 Products (one per `substrate_skus` × `substrate_inventory` row, single Default variant, USD pricing in cents, `manage_inventory=true` so inventory levels can be linked in P3).
  - [x] Idempotency: sales channel by `name`, stock locations by `name`, products by `handle` (slug of SKU). Re-runs print zero `++` lines.
  - [x] Walks the v2 paginated list endpoints (offset/limit, stops on `count` exhaustion or short page) so the seed never silently truncates against a large store.
  - [x] Out of scope (deferred to P3): inventory levels per (variant × stock_location), orders / customers / regions, multi-currency.

- [x] **P3 · Inbound sync** _(branch: `feature/medusa-p3-sync` — open PR pending)_
  - [x] `MedusaAdapter.configured()` overridden to require `MEDUSA_BASE_URL` *plus* `MEDUSA_ADMIN_EMAIL` + `MEDUSA_ADMIN_PASSWORD` — no auth means there's nothing to log into.
  - [x] `MedusaAdapter._login()` lazy-fetches a v2 admin token via `POST /auth/user/emailpass` and caches it on the instance; `_client()` builds a `JsonHttpClient` with the `Bearer <token>` header.
  - [x] `MedusaAdapter._admin_list(endpoint, key, limit, max_rows)` walks `offset` / `limit` until `count` is consumed (uncapped by default; explicit `max_rows` cap surfaces `truncated=True`). Mirrors the Mautic pagination shape so the cockpit's downstream `summary["truncated_domains"]` + `status="partial"` plumbing is uniform across adapters.
  - [x] `MedusaAdapter._live_sync()` pulls Sales Channels, Stock Locations, Products, Orders. Each row caches into `record_cache`; where the seed left a recoverable spine-side id (`metadata.retail_os_store_id`, variant `sku`) we also write an `external_refs` row.
  - [x] `external_url` deep-links per domain: `app/settings/sales-channels`, `app/settings/locations`, `app/products`, `app/orders`.
  - [x] `sync_inbound()` dispatches: configured → `_live_sync`, else → `_mock_sync` (the prior substrate-only behaviour, unchanged).
  - [x] Three new unit tests in `tests/test_integrations.py`: `_admin_list` paginates until `count` consumed, truncation flag fires when `max_rows` hit, `configured()` requires admin creds.
  - [x] `backend/tests/test_integrations_medusa_live.py` — env-gated, three live tests: live sync round-trip, seeded `retail_os_store_id` external_ref round-trip, registry reports `mode=connected`. Skipped automatically when creds aren't set so CI stays mock-only.

- [x] **P4 · Live outbound apply** _(bundled in `feature/track1-finale`)_
  - [x] `MedusaAdapter.LIVE_ACTION_TYPES = {"store_transfer", "fulfillment_routing"}` — every other action_type falls back to base mock-apply, same shape as ERPNext / Mautic.
  - [x] `apply_outbound` mirrors the Mautic dispatcher: configured + supported → `_dispatch_outbound`; adapter rejection lands in `error` (not `draft_created`); outbox `external_id` mirrors the Medusa entity id.
  - [x] `_medusa_record_transfer` (store_transfer) → looks up the from-store stock_location by `metadata.retail_os_store_id`, appends a transfer row to `metadata.retail_os_pending_transfers`, then POSTs the merged metadata back. Medusa v2 has no inter-location transfer primitive; metadata stash is the cleanest auditable signal and the operator can read it directly from the admin UI.
  - [x] `_medusa_record_routing` (fulfillment_routing) → finds the "Retail Demo" sales channel by name and appends to `metadata.retail_os_routing_log`. Same shape.
  - [x] `_payload_marker` — sha256 over sorted-keys JSON of the payload. Re-applying the same outbox row recognises the existing log entry and returns `details.reused=True` instead of duplicating.
  - [x] Five new unit tests in `tests/test_integrations.py` (stubbed `_client`): transfer happy path, transfer idempotency, routing happy path, missing-from_store → error landing, unsupported-type fallback.
  - [x] Two new env-gated live tests in `tests/test_integrations_medusa_live.py` (`MedusaLiveApplyTest`): real `store_transfer` records on the seeded stock location; `fulfillment_routing` records on the seeded sales channel.

- [x] **P5 · Agent-loop UAT** _(branch: `feature/medusa-p6-docs` — bundled with P6, open PR pending)_
  - [x] `docs/uat/2026-04-30-medusa-p5-store-transfer-demo.md` — full walkthrough with two demos (`store_transfer` lands metadata on the from-store stock_location; `fulfillment_routing` lands on the Retail Demo sales channel), CLI-equivalent verification at every step, idempotency check (re-apply → `details.reused=true`), and an acceptance checklist the operator runs before promoting the UAT.
  - [x] Screenshots are an operator follow-up — the doc carries `> **Screenshot:** img/...` placeholders so the captures slot in without changing any other prose.
  - [x] No frontend bug surfaced this round (the rail-filter fix from ERPNext P5 already covers configured-mode external statuses; Medusa goes through the same `PENDING_EXTERNAL_STATUSES` set).

- [x] **P6 · Docs + tests** _(branch: `feature/medusa-p6-docs` — bundled with P5, open PR pending)_
  - [x] README "Running with real Medusa" section: full quick-start (compose / bootstrap / env / seed / sanity curl), per-action-type mapping table (`store_transfer` → metadata stash on from-store stock location, `fulfillment_routing` → metadata stash on Retail Demo sales channel, anything else → base mock-apply), why metadata stashes vs orders/fulfillments/reservations, troubleshooting cheat sheet (slow first build, restart-loop on missing build output, 401 mid-session re-login, partial-sync truncation, Apple Silicon glibc fallback), live-test instructions, and reset path.
  - [x] Live-path tests already env-gated (covered in P3 + P4); README now points at them so CI stays mock-only by design.

## OpenBoxes _(branch: `feature/openboxes-p1-p6` — open PR pending; bundles all six phases per request)_

- [x] **P1 · Local instance**
  - [x] `infra/openboxes/Dockerfile` — `tomcat:9-jdk17` base, downloads the OpenBoxes WAR (~190 MB) at build time from the GitHub release (pinned to `v0.9.7-hotfix1`; `--build-arg OPENBOXES_VERSION=…` to track newer). Multi-arch in code, but the WAR itself is amd64-only — compose pins `platform: linux/amd64` so it runs under emulation on Apple Silicon (slow but functional).
  - [x] `infra/openboxes/docker-compose.yml` — `mysql:5.7` (Liquibase changesets target this exact dialect) + the custom openboxes Tomcat container on `:8082`. Healthcheck on mysql; demo `openboxes-config.properties` mounted into `/root/.grails/`.
  - [x] `infra/openboxes/bootstrap.sh` — waits for mysql + Tomcat, then waits for Liquibase migrations to finish (3-5 min on a fresh demo). No external migrate command — Grails handles it on first boot. Prints admin URL + creds + the `OPENBOXES_BASE_URL` / `OPENBOXES_USERNAME` / `OPENBOXES_PASSWORD` env block.
  - [x] `infra/openboxes/README.md` — quick-start, why custom image (no official Hub image), Apple Silicon caveats, lifecycle table, `/api/*` surface the cockpit will hit, troubleshooting, reset path.
  - [x] Root `Makefile` targets: `openboxes-up`, `openboxes-down`, `openboxes-bootstrap`, `openboxes-seed`, `openboxes-logs`, `openboxes-status`, `openboxes-nuke`. `openboxes-up` does `up -d --build` because the image is local.

- [x] **P2 · Demo seed**
  - [x] `infra/openboxes/seed.py` — REST-based, idempotent, stdlib-only. Creates 5 Locations (one per `substrate_stores`, embeds `[retail-os:<store_id>]` in the description for round-trip — OpenBoxes' Location domain has no JSON metadata field) + 30 Products (one per substrate SKU; idempotent by `productCode == sku`).
  - [x] Auth: `POST /api/login` for the X-Auth-Token, then `X-Auth-Token: <token>` for `/api/*`. Operator can supply `OPENBOXES_API_TOKEN` to bypass the login flow.
  - [x] Out of scope (deferred): inbound shipments seed (substrate models them but OpenBoxes' Shipment lifecycle has stricter validation than we want in a one-shot seed; operator imports bundled sample-data CSVs or seeds by hand for the P5 walkthrough); stock-on-hand levels.

- [x] **P3 · Inbound sync**
  - [x] `OpenBoxesAdapter.configured()` overridden to require `OPENBOXES_BASE_URL` *plus* either `OPENBOXES_API_TOKEN` or the user/password pair.
  - [x] `OpenBoxesAdapter._login()` lazy-fetches a token via `POST /api/login` (handles both `{token}` and `{data:{token}}` response shapes — varies by 0.9.x minor). Caches on the instance. `_client()` builds a `JsonHttpClient` with the `X-Auth-Token` header.
  - [x] `OpenBoxesAdapter._admin_request(path, method, payload)` — single chokepoint with one re-login + retry on 401 (mirror of MedusaAdapter pattern, shared 401-recovery semantics).
  - [x] `OpenBoxesAdapter._live_sync()` pulls Locations, Products, Inbound Shipments. Locations round-trip via the `[retail-os:<store_id>]` description marker; Products via `productCode`; Shipments cached without a clean local_id (substrate doesn't model them in a round-trippable way).
  - [x] `external_url` deep-links per domain: `location/show`, `product/show`, `shipment/show`.
  - [x] `sync_inbound()` dispatches: configured → `_live_sync`, else → `_mock_sync` (the prior substrate-only path, unchanged).

- [x] **P4 · Live outbound apply**
  - [x] `OpenBoxesAdapter.LIVE_ACTION_TYPES = {"po_held", "po_expedited"}`. `store_transfer` falls back to base mock-apply because OpenBoxes' Stock Movement domain has stricter validation than substrate exposes — deferred.
  - [x] `apply_outbound` mirrors the Mautic / Medusa shape: configured + supported → `_dispatch_outbound`; adapter rejection → `error`; outbox `external_id` mirrors the first matched shipment id.
  - [x] `_ob_annotate_shipment` (po_held / po_expedited): list inbound shipments via `/api/shipments?direction=INBOUND`, index by `name` / `shipmentNumber`, post a `Comment` on each match prefixed `[AI Retail OS · <action_type>]` with the operator's reason. Returns the first shipment id + the full list in `details.shipment_ids`.
  - [x] No matching shipment → row lands in `error` with a clear message ("no matching inbound shipments found for N payload po_id(s); run `make openboxes-seed` if the demo isn't loaded.").
  - [x] Five new unit tests in `tests/test_integrations.py` (stubbed `_client`): `configured()` requires auth, `_admin_request` re-logins on 401, `po_held` annotates matching shipments, no-match lands in `error`, unsupported action falls back to base.

- [x] **P5 · Agent-loop UAT**
  - [x] `docs/uat/2026-04-30-openboxes-p5-po-hold-demo.md` — full walkthrough: setup verification, Replenishment proposal, drawer apply, verification in OpenBoxes UI, idempotency note (cockpit's `outbox_actions.status` short-circuits the drawer; manual re-runs WILL append to OpenBoxes Comments since the API has no native dedup), acceptance checklist.
  - [x] Screenshot placeholders for operator follow-up.

- [x] **P6 · Docs + tests**
  - [x] README "Running with real OpenBoxes" section: full quick-start (compose / bootstrap / first-login password change / env wiring / seed / restart), per-action-type mapping table (`po_held` / `po_expedited` → Comment on matching Shipment, `store_transfer` → base mock-apply, anything else → base mock-apply), Apple Silicon emulation caveat, troubleshooting cheat sheet (slow first boot, Liquibase wait, mock-mode auth requirements, 401 mid-session re-login, no-match error, reset path).
  - [x] `backend/tests/test_integrations_openboxes_live.py` — env-gated, three live tests (live sync round-trip, seeded `[retail-os:<store_id>]` external_ref round-trip, registry reports `mode=connected`). Skipped automatically when creds aren't set so CI stays mock-only.

## Akeneo _(branch: `feature/akeneo-p1-p6` — open PR pending; bundles all six phases)_

- [x] **P1 · Local instance**
  - [x] `infra/akeneo/Dockerfile` — `php:8.1-apache`, clones `akeneo/pim-community-standard` v7.0 (override via `--build-arg AKENEO_REF=…`) + composer install. Akeneo doesn't ship an all-in-one Hub image; this is the upstream-recommended CE Docker pattern. Pinned `platform: linux/amd64` in compose because composer pulls a few amd64-only deps.
  - [x] `infra/akeneo/docker-compose.yml` — `mysql:8.0` (CE 7.x targets this dialect) + `opensearchproject/opensearch:2.11.1` (drop-in for ES 7) + custom akeneo Apache+PHP container on `:8083`. Healthchecks on both backing services.
  - [x] `infra/akeneo/bootstrap.sh` — runs `bin/console pim:installer:db` (idempotent), `pim:user:create` (tolerates "already exists" exit cleanly; non-OK errors abort), `pim:oauth-server:create-client retail-os --grant_type=password --grant_type=refresh_token`. Prints admin URL + creds + the `AKENEO_BASE_URL` / `AKENEO_CLIENT_ID` / `AKENEO_SECRET` / `AKENEO_USERNAME` / `AKENEO_PASSWORD` env block.
  - [x] `infra/akeneo/README.md` — quick-start, why custom image, lifecycle, REST surface (`/api/oauth/v1/token` + `/api/rest/v1/categories|products`), Apple Silicon caveats, troubleshooting, reset path.
  - [x] Root `Makefile` targets: `akeneo-up`, `akeneo-down`, `akeneo-bootstrap`, `akeneo-seed`, `akeneo-logs`, `akeneo-status`, `akeneo-nuke`. `akeneo-up` does `up -d --build`.

- [x] **P2 · Demo seed**
  - [x] `infra/akeneo/seed.py` — REST-based, idempotent, stdlib-only. Auth via `POST /api/oauth/v1/token` (Basic-auth client_id:secret + form-encoded `grant_type=password&username=&password=`). Creates 3 Categories (one per `substrate_categories`, parent=master) + 30 Products (one per substrate SKU, family=`default`, `[retail-os:<sku>]` marker embedded in `description` for round-trip). Idempotent via per-resource GET-by-code.
  - [x] Out of scope (deferred): Families / Attributes / AttributeOptions (Akeneo CE's bundled `default` family covers the demo); asset / media; multi-locale enrichment.

- [x] **P3 · Inbound sync**
  - [x] `AkeneoAdapter.configured()` overridden to require `AKENEO_BASE_URL` + `AKENEO_CLIENT_ID` + `AKENEO_SECRET` + `AKENEO_USERNAME` + `AKENEO_PASSWORD`.
  - [x] `AkeneoAdapter._login()` — OAuth2 password-grant via `/api/oauth/v1/token`. Two-layer auth: Basic (client) + body (user). Cached on instance; `_admin_request` 401 → drop + re-login + retry once (mirror of Mautic / Medusa / OpenBoxes).
  - [x] `AkeneoAdapter._api_list(endpoint)` — walks Akeneo's `_links.next` HATEOAS pagination until exhausted. Strips the host so the JsonHttpClient (already configured with the base URL) can request just the path.
  - [x] `AkeneoAdapter._live_sync()` pulls Categories + Products. Categories cache with `code` as both external_id and local_id. Products cache with `identifier` as external_id; local_id recovered from `identifier` (preferred) or the `[retail-os:<sku>]` marker in `values.description.en_US.data` (mirror of OpenBoxes).
  - [x] `external_url` deep-links: `#/configuration/category/tree/<code>`, `#/enrich/product/<sku>`.
  - [x] `sync_inbound()` dispatches: configured → `_live_sync`, else → `_mock_sync` (the prior substrate-only path, unchanged).

- [x] **P4 · Live outbound apply**
  - [x] `AkeneoAdapter.LIVE_ACTION_TYPES = {"pim_enrich"}` — extension point. No agent currently emits this type; the dispatcher is wired so a future Merchandiser-driven copy edit can apply without further adapter work.
  - [x] `apply_outbound` mirrors the established Medusa / Mautic / OpenBoxes shape: configured + supported → `_dispatch_outbound`; rejection → `error`; outbox `external_id` = product SKU.
  - [x] `_akeneo_enrich_product` (pim_enrich) → PATCH `/api/rest/v1/products/{sku}` with `values` / `categories` / `enabled` from the payload. Akeneo's PATCH semantics merge — fields not in the body stay untouched, so the same payload re-runs cleanly.
  - [x] No-sku → row lands in `error` with a clear remediation hint.
  - [x] Four new unit tests in `tests/test_integrations.py`: `configured()` requires OAuth credentials; `_api_list` walks `_links.next` paginated responses; `pim_enrich` PATCHes the product correctly; missing-sku → `error`.

- [x] **P5 · Agent-loop UAT**
  - [x] `docs/uat/2026-04-30-akeneo-p5-pim-enrich-demo.md` — full walkthrough using the substrate stand-in (the future Merchandiser agent isn't built yet). PATCH idempotency note + acceptance checklist + screenshot placeholders for operator follow-up.

- [x] **P6 · Docs + tests**
  - [x] README "Running with real Akeneo PIM" section: full quick-start (compose / bootstrap / OAuth client mint / env wiring / seed / restart), per-action-type table (`pim_enrich` → PATCH product, anything else → base mock-apply), troubleshooting cheat sheet (slow first build, OpenSearch wait, OAuth2 400, 401 mid-session re-login, Apple Silicon caveats, reset path).
  - [x] `backend/tests/test_integrations_akeneo_live.py` — env-gated, three live tests (live sync round-trip with `mode=connected`, seeded category code round-trip, registry reports `mode=connected`). Skipped automatically when `AKENEO_*` creds aren't set or the OAuth2 token round-trip fails so CI stays mock-only by design.

## Superset
- [ ] P1 · Local instance
- [ ] P2 · Demo seed (point at our spine.db / mirror)
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply (mostly N/A — Superset is read-only)
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

---

# Track 2 — Agent mesh upgrade

Goal: instead of `Chief → one specialist → artifact`, run a multi-agent dialogue where specialists peer-review each other's drafts and the Chief only synthesizes after critique converges. Operator should see drafts → critique → revision → final, not just the final report.

Today's loop (`backend/app/agents/chief_of_staff.py`):
- Chief calls `delegate_to_<specialist>` once per turn.
- Specialist tool-calls substrate, writes one artifact, returns final text.
- Chief synthesizes a 5–10-line operator reply.
- No second pass. No critique. Whatever the specialist wrote on first try ships.

Failure modes this fails to catch: shallow analysis, missed evidence, wrong category, miscalibrated confidence, recommendations that violate the policy table the operator hasn't even surfaced yet.

## Phases

- [x] **A1 · Critic agent (single-pass review)** _(merged: `feature/agent-mesh-a1-critic` → main)_
  - [x] `backend/app/agents/critic.py` — read-only Critic specialist with the spec'd system prompt (facts / gaps / risks / counter-rec / overclaim) and a forced `kind="critique"` write_artifact.
  - [x] 11 tools: `read_artifact`, `read_events`, plus the Analyst's read-only spine kit (`query_sales`, `aggregate_by_category`, `daily_sales`, `get_kpis`, `list_categories`, `list_campaigns`, `inventory_health`, `list_orders`) and `write_artifact`.
  - [x] `chief_of_staff.delegate_to_critic` takes `{artifact_id, task?}` and stitches the id into the task so the Critic's first call is always `read_artifact(artifact_id=…)`.

- [ ] **A2 · Drafter / critic round-trip**
  - [ ] Each specialist (Analyst, Pricing, Marketing, Merchandiser, Fulfillment, Replenishment, Store Manager) gains an optional `revise_artifact(original_id, critique_id)` tool that reads its prior draft + the critique and produces a revised artifact (kind suffix `_revised`).
  - [ ] Chief's run-loop is updated: after a specialist returns a draft, Chief invokes Critic, then asks the original specialist to `revise_artifact` if the critique flagged Gaps or Risks.
  - [ ] Convergence rule: max **2 revision rounds**, or stop earlier when the latest critique returns "no material gaps".
  - **Done when:** a single chat turn produces draft → critique → revision artifacts in the spine, all linked, and the operator-facing reply references the *final* revision.

- [ ] **A3 · Multi-specialist debate (where it helps)**
  - [ ] For decisions where specialists naturally disagree (Pricing vs Replenishment on markdowns; Merchandiser vs Marketing on push priority), the Chief can invoke `delegate_to_<peer>` with the first specialist's draft as input and ask for a peer review (`peer_review` tool) — distinct from generic Critic, scoped to that peer's domain expertise.
  - [ ] Peer reviews are written as `peer_review` artifacts and feed into the same revision loop.
  - **Done when:** a markdown plan triggers Pricing draft → Replenishment peer review → Pricing revision → Critic critique → Pricing final, all visible in the trace pane.

- [ ] **A4 · UI surfacing**
  - [ ] Chat turn renders a stacked thread: each artifact tagged by stage (`draft`, `critique`, `peer review`, `revision`, `final`) with an inline "diff" affordance.
  - [ ] Reports tab gets a `stage` column and a filter for `final` only (default), with a toggle to show all stages.
  - [ ] Approval drawer's `apply` button refuses to fire on a non-`final` artifact.
  - **Done when:** operator can scrub through the dialogue and apply only on the converged final.

- [ ] **A5 · Eval harness**
  - [ ] `backend/tests/agents/eval/` — a small suite of seeded scenarios (overstock summer, weekend heatwave, supplier risk, single-store stockout) with a golden answer per scenario.
  - [ ] Each scenario runs the chat turn end-to-end and scores (LLM-as-judge): factual correctness, evidence cited, policy adherence, recommendation quality.
  - [ ] Compare single-pass (mesh disabled) vs multi-pass (mesh enabled). The multi-pass run must score strictly higher on at least 3 of 4 dimensions on at least 3 of the 4 scenarios.
  - **Done when:** numbers exist and are reproducible; mesh stays on by default.

- [ ] **A6 · Cost & latency guardrails**
  - [ ] Per-turn token budget; if mesh would exceed it, automatically downgrade to single-pass and log a `mesh_downgrade` event.
  - [ ] Backoff: max 3 rounds of revision globally, max 2 critic invocations per draft, hard wall-clock cap (e.g. 60s per operator turn).
  - **Done when:** budget config is in `backend/app/config.py`, surfaced in the cockpit's status strip as a small chip when active.

## Open design questions (decide during A1 brainstorming)

- Critic as one universal agent vs N domain critics (pricing-critic, marketing-critic, …)?
- Revision authored by original specialist vs by the Critic itself acting as writer?
- Should the Chief be allowed to ignore the Critic ("override flag") or always defer?
- Streaming UX: render drafts immediately or wait until convergence? (Lean: stream, mark stage; A4 surfaces this.)
- LLM-as-judge for A5: same provider as production? Different provider for less correlation? Quorum?

---

# Track 3 — Native app

Goal: ship a native client (mobile + desktop) that wraps the HQ Console — push notifications for approvals, biometric unlock, offline tape replay. Ride on top of all backend work; should not require backend changes.

Decide between two paths during scoping:

- **Tauri + the existing Vite/React frontend** — desktop-first (macOS/Windows/Linux), thin Rust shell, smallest delta from the web build. Mobile via Tauri 2.0 (iOS/Android) once it stabilizes for our stack.
- **React Native (Expo) re-skin** — mobile-first, native push and biometrics out of the box, but doubles the UI codebase since the Bloomberg-terminal density doesn't translate cleanly to phones. Likely needs a new "operator at the store" layout, not the cockpit.

## Phases (start after at least 2 systems have completed P1–P6)

- [ ] **B1 · Scoping spike** — pick path (Tauri vs RN), validate SSE chat works, write a one-pager.
- [ ] **B2 · Desktop shell (Tauri)** — wraps the Vite build, bundles for mac/win/linux, wires deep-link to a hosted backend or `localhost`.
- [ ] **B3 · Push for approvals** — when an action enters the queue with `approval_required`, fire an OS notification (`tauri-plugin-notification` / APNs / FCM). Tap → opens drawer.
- [ ] **B4 · Biometric unlock** — Touch ID / Face ID gate before drawer apply. Falls back to password.
- [ ] **B5 · Offline tape replay** — last 200 events cached locally; tape reads cache when network is down; sync on reconnect.
- [ ] **B6 · Mobile companion** — phone-shaped layout (no 3-rail cockpit). Approvals + chat + tape only. Either Tauri 2.0 if it's ready or a thin RN shell sharing api.ts.
- [ ] **B7 · Distribution** — signed binaries + an auto-updater (`tauri-plugin-updater`), TestFlight track for iOS if RN.

Out of scope for this track: real-time multi-user collab, video, voice chat with the agent (separate initiative if it ever happens).

---

# Track 4 — MLflow + MLOps

Goal: every operator turn (and every offline eval run) is logged as a tracked experiment with prompts, traces, scored outputs, and the exact provider/model/version. Without this we can't tell whether changes to the agent mesh, the spec, or the model actually made things better.

Pairs naturally with Track 2 A5 (eval harness) — A5 produces scores, MLflow gives them a home, a UI, and a comparison surface.

## Phases

- [ ] **M1 · Local MLflow stack**
  - [ ] `infra/mlflow/docker-compose.yml` — single-host: postgres backend store + minio (or local fs) artifact store + mlflow tracking server on `:5000`.
  - [ ] Makefile targets: `mlflow-up`, `mlflow-down`, `mlflow-status`.
  - **Done when:** `http://localhost:5000` shows the empty MLflow UI.

- [ ] **M2 · Trace logger in the agent run-loop**
  - [ ] `backend/app/llm/tracing.py` wraps each LLM call: log prompt, system, tool list, response, token counts, model, provider, wall-clock, parent run id.
  - [ ] One MLflow run per operator turn; nested runs per specialist delegate; further nesting per critic round (Track 2 A1).
  - [ ] Tags: `agent`, `phase` (draft/critique/revision/final), `provider`, `model`.
  - **Done when:** running the README demo prompt creates one parent run with N nested runs visible in MLflow UI.

- [ ] **M3 · Eval harness pipes to MLflow**
  - [ ] Track 2 A5 scenarios run as MLflow experiments named `eval/<scenario>/<git-sha>`.
  - [ ] Metrics logged: factual_correctness, evidence_cited, policy_adherence, recommendation_quality, latency_ms, tokens_in, tokens_out.
  - [ ] Artifacts logged: full transcript, all generated reports, the judge's reasoning.
  - **Done when:** `mlflow ui` shows side-by-side comparison of single-pass vs multi-pass runs across all scenarios with a green delta on at least 3 of 4 dimensions on at least 3 of 4 scenarios.

- [ ] **M4 · Prompt + model registry**
  - [ ] System prompts for each agent versioned in MLflow Model Registry (or a lightweight equivalent — `prompts/<agent>/v<n>.md` + an MLflow-tracked alias).
  - [ ] At runtime the agent fetches the active prompt by alias (e.g. `Analyst@prod`), with a kill-switch env var to fall back to the file in repo.
  - **Done when:** flipping an alias in MLflow UI changes the next operator turn's behavior without a code deploy.

- [ ] **M5 · Online drift + cost dashboards**
  - [ ] Periodic job logs daily aggregates: per-agent token spend, per-tool error rate, per-scenario score drift vs the eval baseline.
  - [ ] Surface in the cockpit's Reports tab as a "telemetry" kind, or a thin Streamlit/Superset board pointed at MLflow's tracking DB.
  - **Done when:** an unexpected provider switch or prompt regression shows up in the dashboard within 24h.

- [ ] **M6 · CI gate**
  - [ ] On every PR that touches `backend/app/agents/` or `backend/app/llm/`, a CI job runs the small eval suite and posts the MLflow run ids + a delta table as a PR comment.
  - [ ] Block merge if any dimension regresses by more than a configurable threshold (e.g. 5%).
  - **Done when:** a deliberately bad prompt change is blocked by the gate and the PR comment explains why.

## Open design questions (decide during M1 brainstorming)

- MLflow vs Langfuse vs Phoenix? MLflow chosen for breadth (works for non-LLM models too once we add forecasting), but Langfuse has better LLM-trace UX out of the box. Compare during M1.
- Self-host vs MLflow Cloud? Self-host for now; revisit if multiple people need access.
- Where to keep the prompts of record — repo (`prompts/`) or registry-only? Repo + alias lets us code-review prompts.

---

# Track 5 — Agentic Wiki

Goal: agents accumulate a durable, searchable body of knowledge from prior decisions instead of re-deriving everything per turn. Today every operator question starts from zero. The Wiki gives every specialist a way to write down "what we tried, what worked, what didn't, when" and read it back in future turns.

Pairs with Track 2 (mesh) and Track 4 (MLflow) — critique loops surface lessons, MLflow scores let us promote a lesson from "draft" to "settled". The Wiki is the durable substrate those lessons live in.

## Concept

- One markdown page per topic. Topics are agent-coined (`category/summer_apparel/markdown_playbook`, `vendor/breezeco/reliability_notes`, `policy/margin_floors`).
- Pages are versioned (each agent edit = a commit-like event in the spine event log, kind=`wiki_edit`).
- Pages link to the spine event ids and artifact ids they were derived from — every claim has a citation back into the audit log.
- Every specialist gains `wiki_search`, `wiki_read`, `wiki_propose_edit` tools. Edits are draft-only; promotion to `published` requires either operator approval or a Critic agent (Track 2 A1) sign-off.

## Phases

- [ ] **W1 · Storage + spine wiring**
  - [ ] New `wiki_pages` table in `spine.db`: `slug PRIMARY KEY, title, body_md, owner_agent, status (draft/published/deprecated), version, updated_ts, refs_json`.
  - [ ] New `wiki_revisions` table: full history per slug.
  - [ ] Spine event kinds added: `wiki_edit`, `wiki_publish`, `wiki_deprecate`.
  - **Done when:** programmatic create/edit round-trips through the spine.

- [ ] **W2 · Read tools across specialists**
  - [ ] `wiki_search(query, limit)` — keyword + tag search; reuses the existing artifact body search pattern.
  - [ ] `wiki_read(slug)` — returns body + citations + last-updated.
  - [ ] All specialists get both tools by default (read-only). System prompts updated to encourage citing the wiki.
  - **Done when:** Analyst answers a familiar question by quoting a wiki page with the slug surfaced in the trace.

- [ ] **W3 · Write tools (propose-edit)**
  - [ ] `wiki_propose_edit(slug, body_md, refs)` writes a `draft` revision and emits `wiki_edit`.
  - [ ] Drafts surface in the cockpit's approval rail beside outbox actions; operator approves → `published`.
  - **Done when:** an Analyst running a fresh investigation can leave a published learning behind that the next turn reads.

- [ ] **W4 · Critic-gated auto-publish**
  - [ ] When Track 2 A1 Critic is online, drafts that survive critique with no Risks/Gaps auto-publish.
  - [ ] Operator override always wins.
  - **Done when:** a clean draft promotes itself within one turn without operator action; a flagged draft sits in approvals.

- [ ] **W5 · UI surface**
  - [ ] New `[WIKI]` tab in the cockpit's data rail (search + recent edits).
  - [ ] Drawer renders pages with citations as inline links to the source events / artifacts.
  - [ ] Stage chip (`draft` / `published` / `deprecated`) reused from the chip-token system.
  - **Done when:** operator can pin a wiki page from the drawer; pinned slugs surface in the status strip as a chip.

- [ ] **W6 · Cross-agent learning loop**
  - [ ] After every chat turn, a background `Wiki Curator` agent (read-only over the turn's events + new artifacts) decides whether anything is wiki-worthy and proposes drafts.
  - [ ] Rate-limited: max 3 proposals per turn; max 1 proposal per slug per day.
  - [ ] Curator runs use the same MLflow tracing path (Track 4 M2) so we can score whether wiki growth is helping.
  - **Done when:** a week of operator turns produces a non-trivial wiki, and an A/B comparison (Track 4 M3) shows scenarios where the wiki is read score higher.

## Open design questions (decide during W1 brainstorming)

- Slug shape — agent-coined free-form vs constrained namespaces (`category/<x>`, `vendor/<x>`, `policy/<x>`)? Constrained scales better.
- Auth model — should specialists be allowed to edit each other's pages, or only their own (with peer-review via Track 2 A3)?
- Decay — pages need a "last verified" stamp + a way to flag stale entries; otherwise the wiki rots.
- Embedding-based search vs sqlite FTS? Start with FTS; revisit if recall is poor.

---

# Track 6 — GBrain integration

Goal: stand up [GBrain](https://github.com/garrytan/gbrain) as the operator's persistent memory layer over the cockpit. GBrain is a self-wiring knowledge graph + 29-skill kit + Postgres/PGLite-backed brain that exposes 30+ MCP tools (search, get, code-callers, query, ingest, …). Same problem space as Track 5 (Wiki), but a mature off-the-shelf product instead of a roll-our-own minimal version.

Decide during G1 whether GBrain **replaces** Track 5 (the wiki section becomes a thin facade over GBrain pages) or **complements** it (cockpit wiki for agent-coined retail-domain pages, GBrain for the operator's broader persistent brain). Default lean: complement — keep Track 5's `category/<x>` pages spine-native, plug GBrain in via MCP for cross-cutting recall.

## Concept

- One GBrain instance per operator. Backed by PGLite locally; Postgres in any shared deployment.
- Specialists get a small subset of GBrain MCP tools (`gbrain.query`, `gbrain.get`, `gbrain.search`, optionally `gbrain.ingest`). The Critic from Track 2 A1 gets read-only access too — citations from GBrain feed the "Verified" section.
- After each cockpit chat turn, a signal-detector hook ingests the turn (Chief reply + linked artifacts) into GBrain, picking up entities (categories, vendors, stores, SKUs) automatically.
- Reports rendered in the Reports tab can quote GBrain pages; the artifact viewer adds a `cited brain pages` block when present.

## Phases

- [ ] **G1 · Local GBrain stack**
  - [ ] `infra/gbrain/` — install via `git clone + bun install && bun link` (per the repo's own warning against `bun install -g`); PGLite by default, optional Postgres via env.
  - [ ] Makefile targets: `gbrain-up`, `gbrain-down`, `gbrain-doctor`.
  - [ ] `infra/gbrain/README.md` covers install, the `gbrain init` step, the recurring jobs we keep on (ingest, maintain, smoke-test) and the ones we disable for the demo (anything that hits the public web by default).
  - **Done when:** `gbrain query "hello"` runs locally; `gbrain serve` exposes the MCP endpoint over stdio.

- [ ] **G2 · MCP wiring into the backend agents**
  - [ ] `backend/app/llm/mcp.py` — small client that calls `gbrain serve --http --port 8787` (HTTP transport, not stdio, because we're a long-lived service).
  - [ ] New tools on every read-only specialist (Analyst, Critic): `brain_search`, `brain_get`, `brain_query`. These wrap the GBrain MCP tools and return shapes the agents can quote in `write_artifact`.
  - [ ] Auth: a single bearer token, written by `gbrain auth create`, kept in `backend/.env` as `GBRAIN_BEARER`. Adapter health-check pattern from the integrations layer (Track 1) applies.
  - **Done when:** the Analyst can answer "what did we decide about summer apparel last quarter?" by quoting one or more brain pages with their slugs.

- [ ] **G3 · Ingest hook on every chat turn**
  - [ ] When `chief_of_staff.run_chief` finishes a turn, fire a background `signal-detector`-style ingest: pass the operator prompt, Chief reply, and any new artifact bodies into `gbrain ingest`.
  - [ ] Rate-limited per turn (≤ 3 ingest calls) to keep the brain compact and avoid over-indexing.
  - [ ] Spine event kind `brain_ingest` records the turn → brain page mapping for the audit log.
  - **Done when:** a fresh cockpit turn shows up as one or more pages in the brain within ~10s of the assistant's final text.

- [ ] **G4 · Code-graph for the repo**
  - [ ] `gbrain sources add <this-repo> --strategy code` indexes our backend + frontend.
  - [ ] Add to the Critic's tool kit: `code_callers`, `code_callees`, `code_def`, `code_refs`. The Critic can now point at the actual call site that contradicts a draft.
  - **Done when:** asking the Critic "is this Pricing-rule discount within policy?" surfaces the policy-floor check function path + line, not just a vibes answer.

- [ ] **G5 · Cockpit "[BRAIN]" tab**
  - [ ] New tab in the data rail (sits beside `[REPORTS]`): live search box + recent pages, click → drawer renders the page with citations as inline links.
  - [ ] Stage chip (`tier-1` / `tier-2` / `tier-3` per GBrain's enrichment tiers).
  - [ ] Status strip gains a tiny `brain n pages` chip when GBrain is up, otherwise it's hidden (no `mock` chip — the brain is optional).
  - **Done when:** operator can find any prior decision via the cockpit's Brain tab without leaving the cockpit.

- [ ] **G6 · Track 5 / Track 6 reconciliation**
  - [ ] Decision doc: one of (a) deprecate Track 5 Wiki entirely, (b) keep Track 5 for retail-domain `category/<x>` pages and route everything else to GBrain, or (c) implement a thin GBrain back-end for Track 5 (cockpit Wiki tab reads/writes GBrain pages directly).
  - [ ] Kill or fold whichever Track 5 phases the decision retires.
  - **Done when:** TODO has only one durable persistent-memory track.

- [ ] **G7 · Docs + smoke tests**
  - [ ] README "Running with GBrain" section (mirror of the ERPNext one): install, env wiring, sanity query.
  - [ ] `backend/tests/test_gbrain_live.py` — env-gated smoke tests against `localhost:8787` for query / get / ingest.
  - [ ] Note in the cockpit's status strip when `GBRAIN_BEARER` is missing or the endpoint is down — same chip pattern as the integrations row.

## Open design questions (decide during G1)

- PGLite (zero infra) vs Postgres (shareable across operators)? Lean PGLite for the demo, Postgres for any deployed environment.
- Do we re-use the integrations adapter pattern for GBrain, or treat it as a first-class subsystem (its own folder, not an `IntegrationAdapter`)? Probably first-class — GBrain is durable agent memory, not a system of record.
- Which GBrain skills do we pull into our cockpit's agent prompts directly (e.g. `quality.md`, `brain-first.md` cross-cutting rules) vs leave inside GBrain to fire on its own? Lean: import the cross-cutting `conventions/` rules into our system prompts so the cockpit's agents follow the same brain-first lookup discipline.
- Network egress — GBrain has skills that hit the public web for enrichment. Disable in the demo unless the operator opts in. Surface as an explicit toggle in the cockpit.
