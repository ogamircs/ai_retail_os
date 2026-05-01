# OpenBoxes P5 — PO-hold demo UAT log

**Date:** 2026-04-30
**Scope:** End-to-end run of the `po_held` flow against a real OpenBoxes 0.9.x instance: operator approves a Replenishment proposal → live `Comment` lands on each matching inbound Shipment in OpenBoxes.

> **Status:** template walkthrough. Steps + acceptance gates are scripted and reproducible from the CLI; UI screenshots are an operator follow-up. Mark each step with the actual screenshot path under `img/` once captured.

## Setup

Local stack up + seeded:

```bash
make openboxes-up         # builds image (first run pulls ~190MB WAR)
make openboxes-bootstrap  # waits for Liquibase migrations (3-5 min on
                          #   first boot) and prints admin creds
# Sign in at http://localhost:8082 with openboxes / password and
# change the password (OpenBoxes prompts on first login).
make openboxes-seed       # 5 Locations + 30 Products
```

`backend/.env`:

```env
OPENBOXES_BASE_URL=http://localhost:8082
OPENBOXES_USERNAME=openboxes
OPENBOXES_PASSWORD=<your-new-password>
```

Backend on `:8000`, frontend on `:5173`, OpenBoxes on `:8082`. After restarting uvicorn the cockpit's Integrations tab shows the `openboxes` row as `connected`.

Pre-UAT sanity:

```bash
# Token round-trip
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"$OPENBOXES_USERNAME\",\"password\":\"$OPENBOXES_PASSWORD\"}" \
  http://localhost:8082/api/login | head -c 200; echo
# → {"token":"..."}

# Inbound sync
curl -s -X POST http://localhost:8000/api/integrations/openboxes/sync | jq '.result.summary'
# → {mode: connected, domains: {Location: 5, Product: 30, ...}}
```

## Step 1 — Replenishment proposes a hold

Substrate stand-in (matches the chat path):

```python
from app.substrate import omnichannel
omnichannel.hold_or_expedite_po(category="summer_apparel", action="hold", reason="weather risk")
```

Returns the action with the openboxes outbox row in `approval_required`:

```json
{
  "action_id": <N>,
  "external_actions": [
    {"system_id": "erpnext", "status": "approval_required", "external_domain": "Purchase Order"},
    {"system_id": "openboxes", "status": "approval_required", "external_domain": "Purchase Order"}
  ],
  "pos": [{"po_id": "PO-101", ...}, {"po_id": "PO-104", ...}],
  "reason": "weather risk"
}
```

The cockpit Pending rail picks it up immediately. Status strip: `APPROVE n!` chip pulses amber.

> **Screenshot:** `img/openboxes-p5-1-cockpit-before.png`

## Step 2 — Operator opens the drawer

Click `▶ Held inbound POs`.

The drawer renders `payload.pos[*]`, `reason`, plus the two external chips (`erpnext · Purchase Order`, `openboxes · Purchase Order`). Pick the openboxes chip and click `apply → external`.

> **Screenshot:** `img/openboxes-p5-2-drawer-before-apply.png`

## Step 3 — Adapter dispatches

`OpenBoxesAdapter.apply_outbound` → `_ob_annotate_shipment`:

1. `_admin_request("/api/shipments?direction=INBOUND")` — list shipments.
2. Index by `name` / `shipmentNumber`.
3. For each `payload.pos[*].po_id` that matches: `POST /api/shipments/{id}/comments` with `[AI Retail OS · po_held] Held inbound POs — reason: weather risk`.
4. Return the first matched shipment id as canonical `external_id`; full list lives in `result.details.shipment_ids`.

Drawer flashes:

```
applied — Annotated 2 OpenBoxes shipment(s) with the po_held comment.
```

> **Screenshot:** `img/openboxes-p5-3-drawer-after-apply.png`

## Step 4 — Verify in OpenBoxes admin UI

Open `http://localhost:8082/shipment/show/<shipment_id>`. The Comments tab now carries one new entry per applied row:

```
[AI Retail OS · po_held] Held inbound POs — reason: weather risk
```

> **Screenshot:** `img/openboxes-p5-4-openboxes-shipment-comment.png`

## Step 5 — Idempotency

OpenBoxes has no native dedup on Comments — every re-apply will append another `[AI Retail OS · po_held] …` line. The cockpit's `apply_outbound` short-circuits on `outbox_actions.status == draft_created`, so re-clicking `apply → external` from the drawer is a no-op. Manual re-runs (calling the adapter directly) WILL append.

If the operator wants strict shipment-comment dedup, they can pre-grep the comment list before posting — out of scope for this UAT. The substrate-side dedup via `outbox_actions.status` is sufficient for the cockpit's normal flow.

## Result

End-to-end agent loop runs against a real OpenBoxes:

- Replenishment proposes → outbox row in `approval_required`.
- Cockpit drawer surfaces the payload + the two external targets.
- Operator approves the openboxes chip → adapter posts a comment per matched inbound shipment.
- `result.details` carries `shipment_ids` (full list) and `count` (how many landed).
- Cockpit's `outbox_actions.status` flips to `draft_created`, preventing accidental re-posts via the drawer.

## Acceptance checklist

Operator must verify each before promoting this UAT:

- [ ] `make openboxes-bootstrap` succeeds; admin login works at `/`.
- [ ] `make openboxes-seed` completes with zero `++` lines on the second run.
- [ ] `POST /api/integrations/openboxes/sync` reports `mode=connected`. Catalogue domains (`Location`, `Product`) > 0; `Inbound Shipment` may be 0 on a fresh demo.
- [ ] cockpit Pending rail surfaces a `Held inbound POs` row when `hold_or_expedite_po` runs.
- [ ] drawer apply lands a `Comment` on each matching inbound shipment in OpenBoxes (visible in the UI at `/shipment/show/<id>`).
- [ ] `result.details.count` equals the number of `payload.pos[*].po_id` rows that matched a shipment name.
- [ ] no OpenBoxes-side errors in `make openboxes-logs` during the demo.

## Notes

- `_ob_annotate_shipment` matches by shipment `name` / `shipmentNumber`. The `infra/openboxes/seed.py` doesn't currently create shipments — the operator can either import OpenBoxes' bundled sample-data CSVs (which create POs whose names align with the substrate `PO-…` ids), or seed shipments by hand. A future P2 iteration may create them via the API once we pin the exact endpoint shape per OpenBoxes minor.
- Screenshots are an operator follow-up. Replace each `> **Screenshot:** …` block with the captured PNG path under `docs/uat/img/`.
