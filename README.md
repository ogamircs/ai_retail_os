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

## Provider swap

In the header, change the provider dropdown (Anthropic / OpenAI / Google). Identical behavior, different model. Requires the corresponding API key in `.env`.

## What's stubbed (non-goals)

- Real production integrations to POS, OMS, WMS/3PL, CRM/CDP, EDI, ad networks, payment networks, or finance systems
- External-system write-back without explicit approval. Proposed writes go through `outbox_actions` first.
- Loop Scheduler is mocked through measurement tools rather than cron
- Policy & Spec Registry as a separate human-editable surface (currently embedded as system prompts)
- Auth, multi-tenancy
