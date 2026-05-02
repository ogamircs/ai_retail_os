# AI Retail OS — Prototype

A vertical-slice prototype of the "AI Retail OS" architecture: an orchestrator agent ("Chief of Staff") routes operator intent to specialist DRI agents, each acting through a queryable spine (event log, artifact store, knowledge graph) over mocked retail systems of record.

The current PoC is an omnichannel executive control tower: Marketing can push selected categories, Pricing can create promotions and markdowns, Merchandising can rebalance stores, Fulfillment can route demand, Replenishment can hold or expedite inbound POs, Store Manager can create store tasks, and Analyst can measure the closed loop.

## Layer mapping

| Architecture layer | This repo |
|---|---|
| Surfaces — HQ Console | `frontend/` (React + Vite executive cockpit + chat) |
| Agent Mesh — Chief of Staff + specialists | `backend/app/agents/` |
| Data Spine — Event Log · Artifact Store · KG | `backend/app/spine/`, `artifacts/`, `backend/data/spine.db` |
| Substrate — POS · Inventory · Stores · Orders · Campaigns · POs | `backend/app/substrate/` |
| LLM provider abstraction | `backend/app/llm/` (Anthropic / OpenAI / Google) |

## Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env
# Edit .env — set LLM_PROVIDER and the matching API key

# Seed mock data
python -m app.substrate.seed

# Run backend (port 8000)
uvicorn app.main:app --reload
```

Frontend (separate shell):

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

## Demo path

In the chat, type:

> *We're carrying too much summer apparel. Analyze the situation and propose a markdown plan, then hold any inbound POs for affected SKUs.*

You should see:
- Chief of Staff routes to Analyst → Pricing → Replenishment
- Event Log fills with `decision`, `action`, `observation` rows
- 3+ artifacts appear (analysis, markdown plan, replenishment plan)
- `substrate_inventory.price` updates for marked-down SKUs

Then ask:

> *Did the markdowns lift sales for summer apparel?*

Analyst will read events since the markdown timestamp, query sales, and report.

## Omnichannel demo path

The cockpit loads a seeded operating picture with category momentum, campaign queue, action queue, inventory risk, store exceptions, and supplier risk.

In the chat, type:

> *We have excess summer inventory, uneven store demand, and a weekend heatwave. Build a marketing push for the right categories, decide markdowns, route fulfillment, rebalance stores, hold risky inbound POs, and show expected margin impact.*

You should see:
- Chief of Staff routes across Analyst, Marketing, Merchandiser, Pricing & Promo, Fulfillment, Replenishment, and Store Manager as needed
- Marketing creates category-push campaign briefs and can launch mocked campaigns
- Operational agents add proposed/launched/measured work to the action queue
- Event Log records `proposal`, `campaign_launch`, `approval_required`, `action`, and `measurement` rows
- Artifact Store shows only artifacts attached to the current spine events

Then ask:

> *Did the category push work? Measure lift, ROI, margin impact, fulfillment cost, and remaining risks.*

Marketing and Analyst can read campaign records, orders, sales, and prior spine events to produce a post-action readout.

## New read APIs

- `GET /api/kpis`
- `GET /api/categories`
- `GET /api/marketing/campaigns`
- `GET /api/inventory/health`
- `GET /api/stores`
- `GET /api/orders`
- `GET /api/action-queue`
- `GET /api/kg/neighborhood?id=summer_apparel`

## Open-source integration layer

The demo now has a connector layer under `backend/app/integrations/`. It keeps the current mock retail spine as the normalized read model, while adding external-system status, sync telemetry, external ID mapping, record cache, and approval-gated outbound actions.

Default behavior is mock mode. If no external credentials are present, each adapter mirrors the seeded demo data into the integration cache and never mutates an outside system.

Integration endpoints:

- `GET /api/integrations/systems`
- `POST /api/integrations/{system}/sync`
- `GET /api/integrations/sync-runs`
- `GET /api/integrations/records?system=erpnext&domain=Item&local_id=SUM-001`
- `POST /api/integrations/{system}/actions/{action_id}/apply`
- `POST /api/integrations/mautic/webhook`

Supported adapter IDs:

- `erpnext` — ERP, POS invoices, stock, suppliers, purchase orders, pricing/stock-entry drafts
- `mautic` — marketing segments, campaign drafts, webhook telemetry
- `medusa` — ecommerce orders, inventory levels, fulfillment/reservation proposals
- `openboxes` — warehouse/DC inventory, inbound receiving and stock movement proposals
- `akeneo` — product/category enrichment and PIM completeness signals
- `superset` — BI dashboard references over the retail spine

Optional env vars are listed in `backend/.env.example`. Leave them blank for mock mode.

## Running with real ERPNext

The `erpnext` adapter has a full local-stack rollout — the cockpit can drive a real ERPNext v15 instance instead of mocked data. See `infra/erpnext/README.md` for the full setup; quick path:

```bash
# 1. Stack: mariadb + redis + frappe + nginx on :8080
make erpnext-up

# 2. Create the retail.localhost site, install ERPNext,
#    mint Administrator API key + secret (idempotent)
make erpnext-bootstrap
# → prints {"api_key": "...", "api_secret": "..."}

# 3. Wire backend/.env with the printed creds:
#    ERPNEXT_BASE_URL=http://localhost:8080
#    ERPNEXT_API_KEY=...
#    ERPNEXT_API_SECRET=...
#    ERPNEXT_COMPANY=AI Retail OS

# 4. Project the spine demo data into ERPNext (idempotent):
#    1 Company, 3 Item Groups, 30 Items, 5 Warehouses, Suppliers, Brands,
#    Customers, opening Stock Entries, sample POs, draft Sales Invoices.
make erpnext-seed

# 5. (Re)start the backend so it picks up the new env:
cd backend && uvicorn app.main:app --reload
```

Sanity-check auth:

```bash
curl -s http://localhost:8080/api/method/frappe.auth.get_logged_user \
  -H "Authorization: token <ERPNEXT_API_KEY>:<ERPNEXT_API_SECRET>"
# → {"message":"Administrator"}
```

In the cockpit's Integrations tab the `erpnext` row will now show a green `connected` chip. Click `sync` to pull real records into `record_cache` + `external_refs`. Approve a `Pricing & Promo` markdown via the drawer's `apply → external` button — the adapter creates a real draft `Pricing Rule` in ERPNext, visible at `http://localhost:8080/app/pricing-rule`.

A walkthrough with screenshots lives in [`docs/uat/2026-04-30-erpnext-p5-markdown-demo.md`](docs/uat/2026-04-30-erpnext-p5-markdown-demo.md).

### What lands in ERPNext per action type

| `action_type` | ERPNext doctype | Notes |
|---|---|---|
| `promotion` | `Pricing Rule` | Item-Group-scoped, `Discount Percentage`, 30d validity. Draft. |
| `po_held` | `Comment` on each matching `Purchase Order` | Matches by `(supplier, schedule_date)`. |
| `po_expedited` | Comment + best-effort `schedule_date` bumped 3d earlier | Falls back to comment-only on submitted POs. |
| `store_transfer` | `Stock Entry` (Material Transfer) | Picks the top-on-hand SKU as the line item. Draft. |
| `fulfillment_routing` | `Sales Order` | Walk-In customer, qty 1, strategy text in result details. Draft. |

Nothing is auto-submitted — every doc lands as `docstatus=0` so the operator can review in ERPNext before pushing it through accounting / stock.

### Troubleshooting

| Symptom | What to check |
|---|---|
| Cockpit Integrations row stays `mock` | `backend/.env` not picked up by the running uvicorn. Restart the backend. |
| `apply → external` returns `error` chip | Check the row's `result.error` in `outbox_actions`; common: missing Customer (re-run `make erpnext-seed`), missing Brand (likewise), or ERPNext default account currency mismatch (`Debtors - ARO` must be USD). |
| `Could not find Brand: …` on apply | Run `make erpnext-seed` — it creates Brand records for every vendor before items. |
| `Cannot select a Group type Customer Group` | The seed uses `Individual` (a leaf group). Custom edits to the Customer Group tree can re-introduce this — re-run `make erpnext-seed`. |
| `mariadb` container fails to start on Apple Silicon | The compose pins `mariadb:10.6`. If you swap images, keep `--character-set-server=utf8mb4`. |
| ERPNext image tag fails to pull on Apple Silicon | `frappe/erpnext:v15.x.y` patch tags ship amd64-only. The compose pins the multi-arch index digest of the floating `v15` tag for that reason. Bump deliberately. |

### Live-path tests

`backend/tests/test_integrations_erpnext_live.py` exercises sync + apply against a real ERPNext. The tests skip automatically when `ERPNEXT_BASE_URL`/`ERPNEXT_API_KEY`/`ERPNEXT_API_SECRET` aren't set or the endpoint isn't reachable, so CI stays mock-only:

```bash
cd backend
python -m unittest discover -s tests          # 9 mock pass, 6 live skipped
python -m unittest discover -s tests          # 15 pass with .env wired
```

To reset everything from scratch:

```bash
make erpnext-nuke   # stop the stack and wipe volumes
make erpnext-up
make erpnext-bootstrap
make erpnext-seed
```

## Running with real Mautic

The `mautic` adapter has its own local-stack rollout — same shape as ERPNext. See `infra/mautic/README.md` for the full setup; quick path:

```bash
# 1. Stack: mariadb + apache + cron + worker on :8081
make mautic-up

# 2. Run mautic:install once, enable API basic-auth,
#    print admin URL + credentials (idempotent on re-runs)
make mautic-bootstrap
# → prints MAUTIC_BASE_URL / MAUTIC_USERNAME / MAUTIC_PASSWORD

# 3. Wire backend/.env with the printed creds:
#    MAUTIC_BASE_URL=http://localhost:8081
#    MAUTIC_USERNAME=admin
#    MAUTIC_PASSWORD=retail-mautic

# 4. Project the spine demo data into Mautic (idempotent):
#    4 Segments, 20 Contacts (5 personas × 4 segments), N Campaign drafts.
make mautic-seed

# 5. (Re)start the backend so it picks up the new env:
cd backend && uvicorn app.main:app --reload
```

Sanity-check auth:

```bash
curl -s -u "$MAUTIC_USERNAME:$MAUTIC_PASSWORD" \
  "$MAUTIC_BASE_URL/api/contacts?limit=1" | head -c 200; echo
# → {"total":..., "contacts":{...}}
```

In the cockpit's Integrations tab the `mautic` row will show a green `connected` chip. Click `sync` to pull live Segments / Contacts / Campaigns into `record_cache` + `external_refs`. Approve a `Marketing → campaign launch` via the drawer's `apply → external` button — the adapter creates a real draft Campaign in Mautic with the spine `[retail-os:<campaign_id>]` marker embedded so a follow-up sync round-trips it back to the same substrate row.

### What lands in Mautic per action type

| `action_type` | Mautic doc | Notes |
|---|---|---|
| `campaign_launch` | `Campaign` (draft) | `isPublished=false` — Mautic campaigns need events / triggers before publish; the operator wires those in the UI. Description embeds `[retail-os:<campaign_id>]` so re-sync recovers via the P3 marker parser. |
| `campaign_brief` | `Segment` (list) | Idempotent — alias-eq lookup first; only POSTs if absent. Re-applying twice does not duplicate the list. Alias mirrors the seed (`segment_id` with `-` → `_`). |
| `campaign_measurement` | n/a — falls back to base mock-apply | Measurement flows through the existing `POST /api/integrations/mautic/webhook` path; nothing to push. |

Nothing auto-publishes — every Mautic doc lands as a draft so the operator can wire events / approve in the Mautic desk before the campaign goes live.

### Troubleshooting

| Symptom | What to check |
|---|---|
| Cockpit Integrations row stays `mock` | `backend/.env` not picked up by the running uvicorn — restart the backend. |
| `/api/contacts` returns 401 from the bootstrap sanity-check | Re-run `make mautic-bootstrap`; the script always re-applies the `api_enable_basic_auth` toggle (it can drift on Mautic version upgrades). |
| `apply → external` returns `error` chip on `campaign_launch` | Common: payload missing `campaign_id` (lands in `error`, not `draft_created`). Check `outbox_actions.result.error`. |
| Sync returns `status="partial"` with `truncated_domains` populated | A future env knob lowered `max_rows` below the tenant size. Default `_live_sync` uses `max_rows=None` (no cap) — bump or remove the cap; the snapshot is incomplete by design until you do. |
| `seg_loyalists` round-trip not mapping to `seg-loyalists` | Operator-renamed the Mautic alias. The P3 sync's local_id recovery is `alias.replace("_", "-")`; if the alias doesn't follow that scheme it stays unlinked and the cache row carries no spine `local_id`. |
| Apple Silicon: image won't pull | Compose pins `mautic/mautic:5-apache` by index digest (multi-arch). Daily rolling tags occasionally drop arm64 — keep the digest pinned. |
| Want a clean slate | `make mautic-nuke && make mautic-up && make mautic-bootstrap && make mautic-seed` |

### Live-path tests

`backend/tests/test_integrations_mautic_live.py` exercises sync + apply against a real Mautic. Skipped automatically when `MAUTIC_BASE_URL` / `MAUTIC_USERNAME` / `MAUTIC_PASSWORD` aren't set or the endpoint isn't reachable, so CI stays mock-only:

```bash
cd backend
python -m unittest discover -s tests          # mock-only — live tests skipped
python -m unittest discover -s tests          # all pass with backend/.env wired
```

To reset everything from scratch:

```bash
make mautic-nuke   # stop the stack and wipe volumes
make mautic-up
make mautic-bootstrap
make mautic-seed
```

## Running with real Medusa

The `medusa` adapter has its own local-stack rollout. See `infra/medusa/README.md` for the full setup; quick path:

```bash
# 1. Stack: postgres + redis + custom medusa image (first run is slow —
#    docker build clones medusa-starter-default + yarn install, ~3-5 min)
make medusa-up

# 2. Run db:migrate + create admin user (idempotent; aborts loudly on
#    real auth/DB errors so you don't end up with bad creds)
make medusa-bootstrap
# → prints MEDUSA_BASE_URL / MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD

# 3. Wire backend/.env with the printed creds:
#    MEDUSA_BASE_URL=http://localhost:9000
#    MEDUSA_ADMIN_EMAIL=admin@retail.local
#    MEDUSA_ADMIN_PASSWORD=retail-medusa

# 4. Project the spine demo data into Medusa (idempotent):
#    1 Sales Channel, 5 Stock Locations (one per substrate store),
#    30 Products (single Default variant, USD pricing).
make medusa-seed

# 5. (Re)start the backend so it picks up the new env:
cd backend && uvicorn app.main:app --reload
```

Sanity-check auth:

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"email\":\"$MEDUSA_ADMIN_EMAIL\",\"password\":\"$MEDUSA_ADMIN_PASSWORD\"}" \
  "$MEDUSA_BASE_URL/auth/user/emailpass" | head -c 200; echo
# → {"token":"eyJ..."}
```

In the cockpit's Integrations tab the `medusa` row will show a green `connected` chip. Click `sync` to pull live Sales Channels, Stock Locations, Products, and Orders into `record_cache` + `external_refs`. Approve a `Merchandiser → store_transfer` or `Fulfillment → fulfillment_routing` via the drawer's `apply → external` button — the adapter records the action as auditable metadata on the seeded entity (Medusa v2 has no inter-location transfer primitive, so we stash the audit trail directly on the from-store stock location or the Retail Demo sales channel).

A walkthrough with CLI-equivalent verification steps lives in [`docs/uat/2026-04-30-medusa-p5-store-transfer-demo.md`](docs/uat/2026-04-30-medusa-p5-store-transfer-demo.md).

### What lands in Medusa per action type

| `action_type` | Medusa target | What gets written |
|---|---|---|
| `store_transfer` | from-store `Stock Location` | appended to `metadata.retail_os_pending_transfers`. Lookup by `metadata.retail_os_store_id` (the seed stash). |
| `fulfillment_routing` | "Retail Demo" `Sales Channel` | appended to `metadata.retail_os_routing_log`. Lookup by name. |
| anything else | falls back to base mock-apply | configured → `draft_created`, unconfigured → `applied_mock`. No external write. |

Idempotency: each entry carries a `marker = sha256(payload)[:12]` — re-applying the same outbox row finds the existing log entry and returns `details.reused=true` instead of duplicating. Stable across processes.

Why metadata stashes (not orders / fulfillments / reservations)? Medusa v2 has no inter-location transfer primitive, and our outbox payloads describe what the operator wants done — they don't carry a concrete order id to mutate. Inventing fake orders just to write something would be misleading. Metadata on the seeded entity is auditable from the admin UI and round-trips cleanly via the P3 sync.

### Troubleshooting

| Symptom | What to check |
|---|---|
| First `make medusa-up` takes a long time | Image build clones medusa-starter and runs yarn install. ~3–5 min on a fresh machine. Check `make medusa-logs`. |
| Server restart-loops on `Could not find index.html` | The Dockerfile runs `npx medusa build` before `yarn start`; if you're tracking a forked starter that lacks build-time output, re-build with `make medusa-up` (or `--no-cache`). |
| Cockpit Integrations row stays `mock` | `backend/.env` not picked up by the running uvicorn — restart the backend. The override also needs `MEDUSA_ADMIN_EMAIL` + `MEDUSA_ADMIN_PASSWORD` (not just the base URL). |
| `apply → external` returns `error` chip on `store_transfer` | Common: `from_store` missing in the payload, or the seed didn't run so no stock location has the matching `metadata.retail_os_store_id`. Re-run `make medusa-seed`. |
| `apply → external` returns `error` chip on `fulfillment_routing` | "Retail Demo" sales channel is missing — re-run `make medusa-seed`. |
| `/admin/*` calls 401 mid-session | The cockpit caches Medusa's JWT. On 401 the adapter drops the cached token, re-logins via `POST /auth/user/emailpass`, and retries once — verify creds in `backend/.env` if the second attempt also fails. |
| Sync returns `status="partial"` with `truncated_domains` populated | A future env knob lowered `max_rows` below the catalogue size. Default `_live_sync` uses `max_rows=None` (uncapped) — bump or remove the cap. |
| Apple Silicon: image won't build | `node:22-alpine` is multi-arch. If a transitive dep needs glibc, switch to `node:22` (Debian-based) — heavier but compatible. |
| Want a clean slate | `make medusa-nuke && make medusa-up && make medusa-bootstrap && make medusa-seed` |

### Live-path tests

`backend/tests/test_integrations_medusa_live.py` exercises sync + apply against a real Medusa. Skipped automatically when `MEDUSA_BASE_URL` / `MEDUSA_ADMIN_EMAIL` / `MEDUSA_ADMIN_PASSWORD` aren't set or the endpoint isn't reachable, so CI stays mock-only:

```bash
cd backend
python -m unittest discover -s tests          # mock-only — live tests skipped
python -m unittest discover -s tests          # all pass with backend/.env wired
```

To reset everything from scratch:

```bash
make medusa-nuke   # stop the stack and wipe volumes
make medusa-up
make medusa-bootstrap
make medusa-seed
```

## Running with real OpenBoxes

The `openboxes` adapter has its own local-stack rollout. See `infra/openboxes/README.md` for the full setup; quick path:

```bash
# 1. Stack: mysql + custom tomcat-based openboxes image (first run
#    pulls the ~190MB WAR; Liquibase migrations on first boot take 3-5 min)
make openboxes-up

# 2. Wait for Tomcat + Liquibase, then print admin URL + creds
make openboxes-bootstrap
# → prints OPENBOXES_BASE_URL / OPENBOXES_USERNAME / OPENBOXES_PASSWORD

# 3. Sign in once at http://localhost:8082 with openboxes / password,
#    change the password, optionally mint an API token from user-settings.
#
# 4. Wire backend/.env with the printed creds:
#    OPENBOXES_BASE_URL=http://localhost:8082
#    OPENBOXES_USERNAME=openboxes
#    OPENBOXES_PASSWORD=<your-new-password>
#    # OPTIONAL: bypass the login flow once you've minted a token
#    # OPENBOXES_API_TOKEN=...
#
# 5. Project the spine demo data (5 Locations + 30 Products):
make openboxes-seed

# 6. (Re)start the backend so it picks up the new env:
cd backend && uvicorn app.main:app --reload
```

Sanity-check auth (token round-trip):

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"$OPENBOXES_USERNAME\",\"password\":\"$OPENBOXES_PASSWORD\"}" \
  "$OPENBOXES_BASE_URL/api/login" | head -c 200; echo
# → {"token":"..."}  (or {"data":{"token":"..."}}, depending on minor)
```

In the cockpit's Integrations tab the `openboxes` row will show a green `connected` chip. Click `sync` to pull live Locations / Products / Inbound Shipments into `record_cache` + `external_refs`. Approve a `Replenishment → po_held` (or `po_expedited`) via the drawer's `apply → external` button — the adapter posts a `Comment` on each matching inbound Shipment in OpenBoxes, visible at `/shipment/show/<id>`.

A walkthrough with CLI-equivalent verification steps lives in [`docs/uat/2026-04-30-openboxes-p5-po-hold-demo.md`](docs/uat/2026-04-30-openboxes-p5-po-hold-demo.md).

### What lands in OpenBoxes per action type

| `action_type` | OpenBoxes target | What gets written |
|---|---|---|
| `po_held` | inbound `Shipment` | `Comment` per matching shipment, prefixed `[AI Retail OS · po_held]`, carrying the operator's reason. Match key: `payload.pos[*].po_id` ↔ shipment `name` / `shipmentNumber`. |
| `po_expedited` | inbound `Shipment` | same shape as `po_held` but with the `po_expedited` prefix. |
| `store_transfer` | falls back to base mock-apply | OpenBoxes' Stock Movement domain has stricter validation than substrate exposes; deferred to a future phase. |
| anything else | base mock-apply | configured → `draft_created`, unconfigured → `applied_mock`. No external write. |

> **Apple Silicon note:** OpenBoxes 0.9.x WAR is amd64-only. The compose pins `platform: linux/amd64` so it runs under emulation — slow but functional. Expect 2-3× slower than ERPNext / Mautic / Medusa.

### Troubleshooting

| Symptom | What to check |
|---|---|
| First `make openboxes-up` takes a long time | Image build pulls the ~190 MB WAR + Liquibase applies 200+ changesets on first boot. `make openboxes-logs` to watch progress. |
| `make openboxes-bootstrap` times out | First-boot Liquibase isn't done yet. Re-running the bootstrap is safe. |
| Cockpit Integrations row stays `mock` | `backend/.env` not picked up by the running uvicorn — restart. The override needs `OPENBOXES_BASE_URL` *plus* either `OPENBOXES_API_TOKEN` or `OPENBOXES_USERNAME` + `OPENBOXES_PASSWORD`. |
| `/api/login` returns 401 | OpenBoxes prompts for password change on first login. Sign in once via the UI, change the password, then update `OPENBOXES_PASSWORD` in `backend/.env`. |
| `apply → external` returns `error` chip on `po_held` | Most often: `payload.pos[*].po_id` doesn't match any shipment name. Re-run `make openboxes-seed` (or import a CSV that names shipments after the substrate `po_id`s). |
| `/api/*` calls 401 mid-session | The cockpit caches the X-Auth-Token. On 401 the adapter drops the cached token, re-logins, and retries once — verify creds if the second attempt also fails. |
| Apple Silicon: container runs slowly | Expected — OpenBoxes 0.9.x is amd64-only and runs under qemu emulation. |
| Want a clean slate | `make openboxes-nuke && make openboxes-up && make openboxes-bootstrap && make openboxes-seed` |

### Live-path tests

`backend/tests/test_integrations_openboxes_live.py` exercises sync + apply against a real OpenBoxes. Skipped automatically when `OPENBOXES_BASE_URL` isn't set or auth env (token OR user/password) is missing, so CI stays mock-only:

```bash
cd backend
python -m unittest discover -s tests          # mock-only — live tests skipped
python -m unittest discover -s tests          # all pass with backend/.env wired
```

To reset everything from scratch:

```bash
make openboxes-nuke && make openboxes-up && make openboxes-bootstrap && make openboxes-seed
```

## Running with real Akeneo PIM

The `akeneo` adapter has its own local-stack rollout. See `infra/akeneo/README.md` for the full setup; quick path:

```bash
# 1. Stack: mysql 8 + opensearch 2 + custom akeneo image (first run
#    composer-installs ~3000 deps, ~5-10 min)
make akeneo-up

# 2. pim:installer:db + admin user + OAuth2 client (all idempotent)
make akeneo-bootstrap
# → prints AKENEO_BASE_URL / AKENEO_CLIENT_ID / AKENEO_SECRET /
#   AKENEO_USERNAME / AKENEO_PASSWORD

# 3. Wire backend/.env with the printed creds.

# 4. Project the spine demo data (3 Categories + 30 Products):
make akeneo-seed

# 5. (Re)start the backend so it picks up the new env:
cd backend && uvicorn app.main:app --reload
```

Sanity-check auth (OAuth2 password grant):

```bash
curl -s -u "$AKENEO_CLIENT_ID:$AKENEO_SECRET" \
  -X POST "$AKENEO_BASE_URL/api/oauth/v1/token" \
  -d "grant_type=password&username=$AKENEO_USERNAME&password=$AKENEO_PASSWORD" \
  | head -c 200; echo
# → {"access_token":"...","expires_in":3600,...}
```

In the cockpit's Integrations tab the `akeneo` row will show a green `connected` chip. Click `sync` to pull live Categories / Products into `record_cache` + `external_refs`. Categories round-trip via their `code`; Products via their `identifier` (== substrate SKU when seeded). Outbound enrichment is wired through `pim_enrich` (PATCH `/api/rest/v1/products/{code}` with merge semantics) — currently no agent emits this type, but the dispatcher is in place for a future Merchandiser-driven copy edit.

A walkthrough lives in [`docs/uat/2026-04-30-akeneo-p5-pim-enrich-demo.md`](docs/uat/2026-04-30-akeneo-p5-pim-enrich-demo.md).

### What lands in Akeneo per action type

| `action_type` | Akeneo target | What gets written |
|---|---|---|
| `pim_enrich` | `Product` | PATCH `/api/rest/v1/products/{sku}` with `values` / `categories` / `enabled` from the payload. Merge semantics — fields not in the body stay untouched. |
| anything else | base mock-apply | configured → `draft_created`, unconfigured → `applied_mock`. No external write. Akeneo's role in the demo is mostly read-only enrichment surfacing. |

### Troubleshooting

| Symptom | What to check |
|---|---|
| First `make akeneo-up` takes a long time | Composer install runs against ~3000 packages. Watch with `make akeneo-logs`. |
| `pim:installer:db` errors | OpenSearch isn't healthy yet — re-run `make akeneo-bootstrap`. The installer is idempotent. |
| OAuth2 token endpoint returns 400 | Akeneo needs Basic auth (client_id:secret) AND `grant_type=password` body. Missing either fails. |
| `/api/rest/v1/*` returns 401 mid-session | The cockpit caches the bearer. On 401 the adapter re-logins and retries once. |
| Apple Silicon: slow boot | Akeneo's stack pins `platform: linux/amd64` and runs under qemu. Expected. |
| Want a clean slate | `make akeneo-nuke && make akeneo-up && make akeneo-bootstrap && make akeneo-seed` |

### Live-path tests

`backend/tests/test_integrations_akeneo_live.py` exercises sync + apply against a real Akeneo. Skipped automatically when `AKENEO_*` creds aren't set or the OAuth2 round-trip fails, so CI stays mock-only.

## Running with real Superset

The `superset` adapter has its own local-stack rollout. See `infra/superset/README.md` for the full setup; quick path:

```bash
# 1. Stack: postgres 15 + redis 7 + apache/superset:3.1.1
make superset-up

# 2. db upgrade + admin user + roles (idempotent)
make superset-bootstrap
# → prints SUPERSET_BASE_URL / SUPERSET_USERNAME / SUPERSET_PASSWORD

# 3. Wire backend/.env with the printed creds.

# 4. Register spine.db + 3 datasets + 3 charts + 1 dashboard:
make superset-seed

# 5. (Re)start the backend so it picks up the new env:
cd backend && uvicorn app.main:app --reload
```

Sanity-check auth (Flask-AppBuilder JWT):

```bash
curl -s -X POST "$SUPERSET_BASE_URL/api/v1/security/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$SUPERSET_USERNAME\",\"password\":\"$SUPERSET_PASSWORD\",\"provider\":\"db\",\"refresh\":true}" \
  | jq -r '.access_token' | head -c 20; echo
# → eyJ0eXAi...
```

In the cockpit's Integrations tab the `superset` row will show a green `connected` chip. Click `sync` to pull live Databases / Datasets / Charts / Dashboards into `record_cache` + `external_refs`. The seed registers the cockpit's `spine.db` (mounted read-only at `/spine/spine.db` inside the Superset container), so the demo dashboard's numbers track the same SQLite file the cockpit reads.

A walkthrough lives in [`docs/uat/2026-05-01-superset-p5-dashboard-demo.md`](docs/uat/2026-05-01-superset-p5-dashboard-demo.md).

### What lands in Superset per action type

Superset is **read-only** in our architecture. The cockpit's Analyst deep-links into existing dashboards rather than synthesising new ones. `apply_outbound` therefore falls back to the `IntegrationAdapter` base class for every action type:

| `action_type` | Superset target | What happens |
|---|---|---|
| anything | (none) | configured → `draft_created` (marker only, no HTTP call); unconfigured → `applied_mock`. No mutation in Superset. |

If a future Analyst agent ever needs to auto-create dashboards (e.g. one-shot incident reports), the extension point is `SupersetAdapter.LIVE_ACTION_TYPES`, currently empty by design.

### Troubleshooting

| Symptom | What to check |
|---|---|
| `superset db upgrade` hangs | postgres healthcheck races the connection pool. `make superset-down && make superset-up` re-binds. |
| Admin login returns 401 | `SUPERSET_SECRET_KEY` was changed after init — fix: `make superset-nuke && make superset-up && make superset-bootstrap`. |
| `/api/v1/*` returns 401 mid-session | Cached JWT expired. Adapter clears the token and re-logins on 401, retries once. |
| Dashboard shows zero rows | Bind-mount path drift. Check `infra/superset/docker-compose.yml` mounts `../../backend/data:/spine:ro` and the seed registered `sqlite:////spine/spine.db` (four slashes — it's an absolute path). |
| Apple Silicon | `apache/superset:3.1.1` is multi-arch — no platform pin needed. |
| Want a clean slate | `make superset-nuke && make superset-up && make superset-bootstrap && make superset-seed` |

### Live-path tests

`backend/tests/test_integrations_superset_live.py` exercises live sync against a real Superset. Skipped automatically when `SUPERSET_*` creds aren't set or the login round-trip fails, so CI stays mock-only.

## Running with real GBrain

GBrain (Track 6) is the cockpit's optional persistent agent memory layer — a Bun-native HTTP MCP server installed locally per the upstream pattern (`git clone + bun install + bun link`, no Docker). The cockpit runs in **mock mode by default** — without `GBRAIN_BEARER` set, the Critic's and Analyst's `brain_search` / `brain_read` / `brain_query` tools and the cockpit's `[BRAIN]` tab fall back to a substrate-backed view of `wiki_pages`, so the demo path is coherent without any external dependency.

```bash
# 1. Install GBrain locally (per its README — bun install -g is discouraged)
git clone https://github.com/garrytan/gbrain.git ~/gbrain
cd ~/gbrain
bun install
bun link
gbrain init

# 2. Mint a bearer token + start the HTTP server
gbrain auth create --name "ai-retail-os" --print
make gbrain-up   # foreground; runs `gbrain serve --http --port 8787`

# 3. Wire backend/.env
#    GBRAIN_BASE_URL=http://localhost:8787
#    GBRAIN_BEARER=<token from step 2>

# 4. Restart the backend to pick up the env
uvicorn app.main:app --reload
```

Status strip chip flips from `brain mock` to `brain N pages` once the cockpit can reach the live endpoint. The Critic-side `code_callers` / `code_callees` / `code_def` / `code_refs` tools start returning real results once `gbrain sources add <this-repo> --strategy code` has been run against the codebase (operator-initiated; not part of `make gbrain-up`).

| Cockpit tool | Mock mode (no creds) | Live mode |
|---|---|---|
| `brain_search` | falls back to `wiki.search_pages` | GBrain `/v1/search` |
| `brain_read`   | falls back to `wiki.get_page`     | GBrain `/v1/get`    |
| `brain_query`  | top wiki page body excerpt        | GBrain `/v1/query` (synthesized answer + citations) |
| `code_callers` / `code_callees` / `code_def` / `code_refs` | `{mock: true, results: []}` | GBrain `/v1/code/<kind>` |
| chat-turn ingest hook | logs `brain_ingest` event with mock=true (no external write) | POSTs to GBrain `/v1/ingest` in a daemon thread |

### Disabled jobs

GBrain ships several recurring jobs (`gbrain jobs list`). For the cockpit demo, disable any that hit the public web by default — they cost LLM calls and aren't required:

```bash
gbrain jobs disable web-research
gbrain jobs disable smoke-test-public
# Keep on:
#   ingest    — pulls cockpit chat turns when G3's hook fires
#   maintain  — compacts the brain corpus weekly
```

### Reset

```bash
gbrain doctor              # diagnostics
gbrain corpus rm --confirm # nuke the local PGLite store
gbrain init                # bootstraps fresh
```

The cockpit is unaffected — when the brain endpoint stops responding, the tools and tab fall back to mock automatically.

### Track 5 vs Track 6

Wiki (Track 5) keeps owning retail-domain pages (`category/*`, `vendor/*`, `policy/*`). Brain (Track 6) owns the operator's broader durable memory + code-graph. Full reasoning in `docs/track6/2026-05-02-track5-track6-reconciliation.md`.

### Live-path tests

`backend/tests/test_track6_gbrain.py` exercises the MCP client against a real GBrain when `GBRAIN_BEARER` is set; otherwise the live test is skipped and only the mock-mode tests run, so CI stays brain-less by design.

## Provider swap

In the header, change the provider dropdown (Anthropic / OpenAI / Google). Identical behavior, different model. Requires the corresponding API key in `.env`.

## What's stubbed (non-goals)

- Real production integrations to POS, OMS, WMS/3PL, CRM/CDP, EDI, ad networks, payment networks, or finance systems
- External-system write-back without explicit approval. Proposed writes go through `outbox_actions` first.
- Loop Scheduler is mocked through measurement tools rather than cron
- Policy & Spec Registry as a separate human-editable surface (currently embedded as system prompts)
- Auth, multi-tenancy
