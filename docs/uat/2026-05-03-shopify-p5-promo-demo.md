# Shopify Plus P5 — Promotion + campaign-brief demo UAT log

**Date:** 2026-05-03
**Scope:** End-to-end run of the Pricing & Promo and Marketing flows against a real Shopify Plus dev store: operator approves an agent proposal → live Discount lands in `Discounts → Automatic` and a campaign brief lands as a Shop-level metafield.

> **Status:** template walkthrough. Steps + acceptance gates are scripted and reproducible from the CLI; UI screenshots are an operator follow-up. Mark each step with the actual screenshot path under `img/` once captured.

## Setup

Provision a free Shopify Partner dev store and mint a custom-app token per `infra/shopify/README.md`. There is no local stack — Shopify hosts everything.

`backend/.env` carries the dev-store credentials:

```env
SHOPIFY_SHOP_DOMAIN=ai-retail-os-demo.myshopify.com
SHOPIFY_ADMIN_TOKEN=shpat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SHOPIFY_API_VERSION=2025-01
# Optional — flips campaign_brief from Shop metafield stash to Klaviyo POST:
# KLAVIYO_API_KEY=pk_demo_xxxxxxxxxxxx
```

After restarting uvicorn the cockpit's Integrations tab should show the `shopify` row as `connected`. Backend on `:8000`, frontend on `:5173`.

Pre-UAT sanity (auth + sync):

```bash
# Token round-trip — should print the shop name
curl -sS -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_TOKEN" \
  "https://$SHOPIFY_SHOP_DOMAIN/admin/api/$SHOPIFY_API_VERSION/shop.json" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["shop"]["name"])'

# Seed the dev store (idempotent — re-runs print zero ++ lines)
make shopify-seed

# Inbound sync via the cockpit adapter — should return
# {status: success, mode: connected, domains: {Location: 5, Product: 30, Inventory Level: …, Order: …}}
curl -s -X POST http://localhost:8000/api/integrations/shopify/sync | python3 -m json.tool | head -30
```

## Demo A — promotion (Pricing & Promo → automatic discount)

### Step 1 — Pricing & Promo proposes a markdown

Substrate stand-in (matches the chat path the cockpit actually drives once the Pricing agent picks `summer_apparel` as the over-stocked category):

```python
from app.substrate import omnichannel
omnichannel.recommend_promotion(category="summer_apparel", discount_pct=0.25)
```

Returns the action plus the `shopify` outbox row in `approval_required`:

```json
{
  "action_id": <N>,
  "external_actions": [
    {"system_id": "erpnext", "status": "approval_required", "external_domain": "Promotion"},
    {"system_id": "shopify", "status": "approval_required", "external_domain": "Discount"},
    {"system_id": "mautic",  "status": "mock_only",         "external_domain": "Campaign"}
  ],
  "category": "summer_apparel",
  "discount_pct": 0.25
}
```

The cockpit's Pending rail picks it up immediately. Status strip: `APPROVE n!` chip pulses amber.

> **Screenshot:** `img/shopify-p5-1-cockpit-before.png`

### Step 2 — Operator opens the drawer

Click the `▶ Promote summer apparel` row.

The drawer renders the payload (`category=summer_apparel`, `discount_pct=0.25`) and shows the external targets — pick the **shopify · Discount · approval_required** chip to apply only to Shopify. (`erpnext` and `mautic` are independent rows on the same action.)

> **Screenshot:** `img/shopify-p5-2-drawer-before-apply.png`

### Step 3 — Apply

Hit **Approve & apply**. The drawer's progress chip flips:

- `approval_required` → `applying` → `draft_created`
- The result panel shows the Shopify `automaticDiscountNode` numeric id (e.g. `9001`) and the deep-link `https://ai-retail-os-demo.myshopify.com/admin/discounts/9001`.

> **Screenshot:** `img/shopify-p5-3-drawer-after-apply.png`

### Step 4 — Verify in Shopify Admin

In the dev store admin → **Discounts** → **Automatic**:

- New row titled `AI Retail OS — Promote summer apparel (summer_apparel)`
- Method: `Automatic`
- Type: `Amount off products`
- Status: `Active`
- Value: `25% off`
- Active dates: today → today + 30d
- Eligibility: `All customers`
- Items: `All products`

> **Screenshot:** `img/shopify-p5-4-shopify-discount-list.png`
> **Screenshot:** `img/shopify-p5-5-shopify-discount-detail.png`

### Idempotency

The cockpit's `outbox_actions.status` short-circuits the drawer — once an action is `draft_created`, the apply button is disabled. Manually re-running the same outbox row via `registry.apply_outbound("shopify", N)` returns the existing row without re-hitting Shopify.

A *new* outbox row with the same payload **will** create a second discount in Shopify because `discountAutomaticBasicCreate` has no native dedup. This is by design — the cockpit treats every approved action as a fresh intent.

## Demo B — campaign_brief (Marketing → Shop metafield stash)

### Step 1 — Marketing proposes a campaign

```python
from app.substrate import omnichannel
omnichannel.recommend_campaign(category="summer_apparel", segment_id="seg_vacation")
```

Returns the action with `shopify · Campaign · approval_required`.

### Step 2 — Apply the Shopify row

Same drawer flow as Demo A.

Without `KLAVIYO_API_KEY`:

- The brief lands as an entry in the `retail_os.campaign_briefs` JSON metafield on the **Shop** entity.
- `external_id` is the payload-hash marker (e.g. `pa3c8…`).
- Re-applying the same payload (via a new outbox row) detects the marker in the existing metafield list and returns `details.reused=true` instead of duplicating the entry.

With `KLAVIYO_API_KEY` set:

- The brief POSTs to `https://a.klaviyo.com/api/campaigns/` and lands as a draft Klaviyo campaign.
- `external_id` is the Klaviyo `campaign.id`.
- `details.channel` reads `klaviyo` instead of `shopify-shop-metafield`.

### Step 3 — Verify

Shopify metafield path:

- Shopify Admin → **Settings** → **Custom data** → **Shop** → **retail_os** → `campaign_briefs` (JSON).
- Open it: an array of `{marker, title, payload}` entries.

> **Screenshot:** `img/shopify-p5-6-shop-metafield.png`

Klaviyo path:

- Klaviyo Dashboard → **Campaigns** → filter by name `AI Retail OS —` → new draft row.

> **Screenshot:** `img/shopify-p5-7-klaviyo-draft.png`

## Acceptance checklist

Run before promoting the UAT.

- [ ] `make shopify-seed` re-run prints zero `++` lines (idempotent).
- [ ] Cockpit Integrations tab: `shopify` row shows `mode=connected` and a recent `last sync` timestamp.
- [ ] Pricing & Promo demo: real automatic discount lands in `Discounts → Automatic`, deep-link in the drawer opens to the correct row.
- [ ] Re-running the apply on the same outbox row is a no-op (drawer disables the button); `apply_outbound` returns the existing draft.
- [ ] Marketing demo (no Klaviyo): `retail_os.campaign_briefs` metafield on Shop carries the new entry; re-apply via fresh outbox row marks `details.reused=true`.
- [ ] Marketing demo (with Klaviyo): draft campaign appears in the Klaviyo dashboard with title `AI Retail OS — …`.
- [ ] Frontend rail-filter parity: `shopify` `approval_required` rows appear in the Pending rail (existing `PENDING_EXTERNAL_STATUSES` set already covers configured-mode external statuses; no rail-filter regression expected).

## Cleanup

Delete the test discount + drop the metafield + (optionally) recycle the dev store via the path documented in `infra/shopify/README.md` → **Reset path**.
