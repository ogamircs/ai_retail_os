# Medusa P5 — Store-transfer demo UAT log

**Date:** 2026-04-30
**Scope:** End-to-end run of the store-transfer + fulfillment-routing flows against a real Medusa v2 instance: operator approves a Merchandiser proposal → live metadata stash lands on the seeded stock_location / sales_channel.

> **Status:** template walkthrough. Steps + acceptance gates are scripted and reproducible from the CLI; UI screenshots are an operator follow-up. Mark each step with the actual screenshot path under `img/` once captured.

## Setup

Local stack up + seeded:

```bash
make medusa-up         # builds medusa image (~3-5 min first run)
make medusa-bootstrap  # db:migrate + admin user
make medusa-seed       # 1 sales channel + 5 stock locations + 30 products
```

`backend/.env` carries the admin creds the bootstrap printed:

```env
MEDUSA_BASE_URL=http://localhost:9000
MEDUSA_ADMIN_EMAIL=admin@retail.local
MEDUSA_ADMIN_PASSWORD=retail-medusa
```

Backend on `:8000`, frontend on `:5173`, Medusa on `:9000`. After restarting uvicorn, the cockpit's Integrations tab should show the `medusa` row as `connected`.

Pre-UAT sanity (auth + sync):

```bash
# Token round-trip
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"email":"admin@retail.local","password":"retail-medusa"}' \
  http://localhost:9000/auth/user/emailpass | jq -r '.token' | head -c 20; echo
# → eyJhbGciOi...

# Inbound sync via the cockpit's adapter — should report
# {status: success, mode: connected, domains: {Sales Channel: 1, Stock Location: 5, Product: 30, Order: 0}}
curl -s -X POST http://localhost:8000/api/integrations/medusa/sync | jq '.result.summary'
```

## Demo A — store_transfer

### Step 1 — Merchandiser proposes a transfer

Substrate stand-in (matches the chat path the cockpit actually drives):

```python
from app.substrate import omnichannel
omnichannel.recommend_store_transfer(category="summer_apparel")
```

Returns the action + the `medusa` outbox row in `approval_required`:

```json
{
  "action_id": <N>,
  "external_actions": [
    {"system_id": "erpnext", "status": "approval_required", "external_domain": "Stock Entry"},
    {"system_id": "medusa", "status": "approval_required", "external_domain": "Reservation"},
    {"system_id": "openboxes", "status": "mock_only", "external_domain": "Stock Movement"}
  ],
  "from_store": "sto-chi",
  "to_store": "sto-mia",
  "qty": 24
}
```

The cockpit's Pending rail picks it up immediately. Status strip: `APPROVE n!` chip pulses amber.

> **Screenshot:** `img/medusa-p5-1-cockpit-before.png` — cockpit with the `Rebalance category inventory` row + medusa `connected` chip.

### Step 2 — Operator opens the drawer

Click the `▶ Rebalance category inventory` row.

The drawer renders the payload (`from_store=sto-chi`, `to_store=sto-mia`, `qty=24`, `reason=…`) and shows three external targets — pick the **medusa · Reservation · approval_required** chip if you want to apply only to Medusa. (`erpnext` and `openboxes` are independent rows on the same action.)

> **Screenshot:** `img/medusa-p5-2-drawer-before-apply.png`

### Step 3 — Operator clicks `apply → external` on the medusa chip

Backend dispatches `MedusaAdapter.apply_outbound` → `_medusa_record_transfer`:

1. `_admin_request("/admin/stock-locations")` — find the location whose `metadata.retail_os_store_id == "sto-chi"`.
2. Compute `marker = sha256(payload)[:12]` — used for idempotency.
3. Append `{marker, title, to_store, category, qty, reason}` to `metadata.retail_os_pending_transfers`.
4. `POST /admin/stock-locations/<id>` with the merged metadata.

Drawer flashes:

```
applied — Recorded transfer on stock_location <loc_id>.
```

> **Screenshot:** `img/medusa-p5-3-drawer-after-apply.png`

### Step 4 — Verify in Medusa admin UI

Open `http://localhost:9000/app/settings/locations`. Click the Chicago store. The right-hand metadata panel now carries:

```json
{
  "retail_os_store_id": "sto-chi",
  "region": "midwest",
  "retail_os_pending_transfers": [
    {
      "marker": "p<12hex>",
      "title": "Rebalance category inventory",
      "to_store": "sto-mia",
      "category": "summer_apparel",
      "qty": 24,
      "reason": "rebalance inventory toward stronger local demand"
    }
  ]
}
```

> **Screenshot:** `img/medusa-p5-4-medusa-stock-location-metadata.png`

### Step 5 — Idempotency check

Click `apply → external` a second time on the same outbox row (or call `POST /api/integrations/medusa/actions/<id>/apply` directly if the cockpit short-circuits). Outcome:

```json
{
  "status": "draft_created",
  "external_id": "<loc_id>",
  "result": {
    "details": {"reused": true, "marker": "p<12hex>"}
  }
}
```

The metadata array is **not duplicated** — the marker matched and the helper returned the existing entry.

## Demo B — fulfillment_routing

### Step 1 — Fulfillment proposes a routing

```python
from app.substrate import omnichannel
omnichannel.route_fulfillment(category="summer_apparel")
```

Returns the outbox row targeting `medusa` (`Fulfillment` domain).

### Step 2 — Apply

`MedusaAdapter._medusa_record_routing`:
1. Find the sales channel named `Retail Demo`.
2. Append `{marker, title, category, strategy, guardrail}` to `metadata.retail_os_routing_log`.
3. POST merged metadata to `/admin/sales-channels/<id>`.

### Step 3 — Verify

Open `http://localhost:9000/app/settings/sales-channels`. Click `Retail Demo`. Metadata panel:

```json
{
  "retail_os_routing_log": [
    {
      "marker": "p<12hex>",
      "title": "Route omnichannel demand",
      "category": "summer_apparel",
      "strategy": "favor BOPIS in high-demand regions and ship-from-store from overstocked locations",
      "guardrail": "avoid stores with labor pressure above 0.78"
    }
  ]
}
```

> **Screenshot:** `img/medusa-p5-5-medusa-sales-channel-metadata.png`

## Result

End-to-end agent loop runs against a real Medusa v2:

- Merchandiser / Fulfillment proposes → outbox row in `approval_required`.
- Cockpit drawer surfaces the payload + the external target.
- Operator approves → `MedusaAdapter.apply_outbound` posts a metadata stash on the seeded entity.
- Re-applying the same row returns `details.reused=true`; no duplicate log entry.
- Subsequent `POST /api/integrations/medusa/sync` round-trips the metadata back via `record_cache`, so the cockpit Reports tab shows the same action history without leaving the UI.

## Acceptance checklist

Operator must verify each before promoting this UAT:

- [ ] `make medusa-bootstrap` succeeds; admin login works at `/app`.
- [ ] `make medusa-seed` completes with zero `++` lines on the second run.
- [ ] `POST /api/integrations/medusa/sync` reports `mode=connected`, all four domains (`Sales Channel`, `Stock Location`, `Product`, `Order`) > 0.
- [ ] cockpit Pending rail surfaces a `Rebalance category inventory` row when `recommend_store_transfer` runs.
- [ ] drawer apply lands a metadata entry under `retail_os_pending_transfers` on the from-store stock_location, visible in the Medusa admin UI.
- [ ] second apply on the same row returns `details.reused=true`; metadata array length unchanged.
- [ ] `route_fulfillment` apply lands an entry under `retail_os_routing_log` on the Retail Demo sales channel.
- [ ] no Medusa-side errors in `make medusa-logs` during the demo.

## Notes

- The metadata-stash design is intentional. Medusa v2 has no inter-location transfer primitive and the outbox payloads carry no concrete order id; inventing fake orders would be misleading. Stash on a seeded entity is auditable from the admin UI and round-trips cleanly via P3 sync. (See README "Why metadata stashes" for the longer rationale.)
- Screenshots are an operator follow-up. Replace each `> **Screenshot:** …` block with the captured PNG path under `docs/uat/img/`.
