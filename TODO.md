# TODO — Real-system rollout

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

- [ ] **P3 · Inbound sync**
  - [ ] Set `ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`, `ERPNEXT_COMPANY` in `backend/.env`
  - [ ] Hit `POST /api/integrations/erpnext/sync` from the cockpit Integrations tab
  - [ ] `record_cache` + `external_refs` populate; `substrate_inventory.on_hand` reflects real Bins
  - [ ] `backend/tests/test_integrations_erpnext_live.py` (env-gated; skipped without creds)
  - **Done when:** cockpit Integrations row shows `mode=connected · last_status=success · records_written>0`

- [ ] **P4 · Live outbound apply**
  - [ ] Override `ERPNextAdapter.apply_outbound` in `backend/app/integrations/systems.py`
    - [ ] `promotion` → POST `/api/resource/Pricing Rule` (draft, not submitted)
    - [ ] `po_held` → PATCH `/api/resource/Purchase Order/{name}` (status `On Hold` or add comment)
    - [ ] `po_expedited` → comment + bumped schedule_date
    - [ ] `store_transfer` → POST `/api/resource/Stock Entry` (Material Transfer, draft)
    - [ ] `fulfillment_routing` → POST `/api/resource/Sales Order` (draft)
  - [ ] Store returned `name` as `external_id` in `outbox_actions.external_id`
  - **Done when:** cockpit drawer apply produces a draft visible in the ERPNext desk; round-trip `external_id` stored

- [ ] **P5 · Agent-loop UAT**
  - [ ] Run README markdown demo against real ERPNext
  - [ ] Approve markdown → real Pricing Rule created
  - [ ] Capture before/after screenshots in PR

- [ ] **P6 · Docs + tests**
  - [ ] README section "Running with real ERPNext" (compose, seed, env, sanity curl)
  - [ ] Troubleshooting (auth, schedule_date format, doctype permissions, port conflicts)
  - [ ] Live-path test cases env-gated; CI stays mock-only

---

## Mautic
- [ ] P1 · Local instance
- [ ] P2 · Demo seed
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

## Medusa
- [ ] P1 · Local instance
- [ ] P2 · Demo seed
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

## OpenBoxes
- [ ] P1 · Local instance
- [ ] P2 · Demo seed
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

## Akeneo
- [ ] P1 · Local instance
- [ ] P2 · Demo seed
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

## Superset
- [ ] P1 · Local instance
- [ ] P2 · Demo seed (point at our spine.db / mirror)
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply (mostly N/A — Superset is read-only)
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests
