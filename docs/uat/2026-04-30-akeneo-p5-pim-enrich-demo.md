# Akeneo P5 — PIM enrichment demo UAT log

**Date:** 2026-04-30
**Scope:** End-to-end run of the (forthcoming) `pim_enrich` flow against a real Akeneo PIM CE 7.x instance: operator approves a Merchandiser copy edit → live PATCH lands on the matching Product.

> **Status:** template walkthrough. The `pim_enrich` action type is currently emitted by no agent — the dispatcher is in place so a future Merchandiser-driven copy edit can apply via PATCH `/api/rest/v1/products/{sku}` without further adapter work. This doc covers the substrate-stand-in path until that agent ships.

## Setup

```bash
make akeneo-up         # ~5-10 min first run (composer install)
make akeneo-bootstrap  # admin user + OAuth2 client
make akeneo-seed       # 3 Categories + 30 Products
```

`backend/.env`:

```env
AKENEO_BASE_URL=http://localhost:8083
AKENEO_CLIENT_ID=...
AKENEO_SECRET=...
AKENEO_USERNAME=admin
AKENEO_PASSWORD=retail-akeneo
```

Pre-UAT sanity:

```bash
# OAuth2 token round-trip
curl -s -u "$AKENEO_CLIENT_ID:$AKENEO_SECRET" \
  -X POST "$AKENEO_BASE_URL/api/oauth/v1/token" \
  -d "grant_type=password&username=$AKENEO_USERNAME&password=$AKENEO_PASSWORD" \
  | jq -r '.access_token' | head -c 20; echo
# → eyJ0eXAi...

# Inbound sync via the cockpit
curl -s -X POST http://localhost:8000/api/integrations/akeneo/sync | jq '.result.summary'
# → {mode: connected, domains: {Category: 3, Product: 30}}
```

## Demo — pim_enrich (manual stand-in)

The chat path is gated behind a Merchandiser agent that hasn't been built. Use the substrate stand-in:

```python
from app.integrations import store, registry

# Create the outbox row a future Merchandiser would emit:
row = store.create_outbox_action(
    system_id="akeneo",
    action_queue_id=None,
    agent="Merchandiser",
    action_type="pim_enrich",
    title="Refresh SKU-001 copy",
    external_domain="Product",
    payload={
        "sku": "SKU-001",
        "values": {
            "name": [{"locale": "en_US", "scope": None, "data": "Lightweight Summer Tee — Refreshed"}],
            "description": [{
                "locale": "en_US", "scope": None,
                "data": "[retail-os:SKU-001] AI-edited copy: breathable, packable, weekend-ready.",
            }],
        },
    },
    configured=True,
)

# Apply via the cockpit's normal route (the drawer will short-circuit
# any second click via outbox_actions.status):
result = registry.apply_outbound("akeneo", row["id"])
print(result)
```

Expected outcome:

```json
{
  "status": "draft_created",
  "external_id": "SKU-001",
  "result": {
    "message": "Akeneo product SKU-001 enriched.",
    "external_domain": "Product",
    "details": {"sku": "SKU-001", "fields": ["name", "description"]}
  }
}
```

> **Screenshot:** `img/akeneo-p5-1-product-edit-before.png`
> **Screenshot:** `img/akeneo-p5-2-product-edit-after.png`

Verify in Akeneo UI: open `http://localhost:8083/#/enrich/product/SKU-001`. The `name` and `description` fields now show the new copy. PATCH semantics mean other attributes (categories, enabled, family-default values) are untouched.

## Idempotency

PATCH is naturally idempotent on the same payload — re-applying writes the same values. The cockpit short-circuits on `outbox_actions.status == draft_created` so the drawer's apply button is one-shot.

## Result

End-to-end PIM enrichment loop runs against real Akeneo:

- (Future) Merchandiser proposes copy → outbox row in `approval_required`.
- Cockpit drawer surfaces the payload + diff against current Akeneo state.
- Operator approves → `AkeneoAdapter.apply_outbound` PATCHes the product.
- Akeneo's PATCH semantics merge — only the fields in the body change.

## Acceptance checklist

- [ ] `make akeneo-bootstrap` succeeds; admin login works at `/`.
- [ ] `make akeneo-seed` completes with zero `++` lines on the second run.
- [ ] `POST /api/integrations/akeneo/sync` reports `mode=connected`. `Category` and `Product` domains > 0.
- [ ] OAuth2 token round-trip returns an `access_token`.
- [ ] PATCH stand-in (above) returns `status=draft_created` and the new copy is visible in the Akeneo UI.
- [ ] no Akeneo-side errors in `make akeneo-logs` during the demo.

## Notes

- Screenshots are an operator follow-up. Replace each `> **Screenshot:** …` block with the captured PNG path under `docs/uat/img/`.
- The Merchandiser agent that emits `pim_enrich` is out of scope for Track 1 — likely a Track 2 mesh-upgrade item (the agent reads Akeneo via `_live_sync` already; the writer half lands later).
