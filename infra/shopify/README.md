# Shopify Plus — local dev/demo

Shopify is **hosted by Shopify**. Unlike `infra/erpnext/` or `infra/medusa/`, there is no Docker image or compose file — you provision a free Partner dev store and point the cockpit at it via `.env`.

## Quick start

1. Sign up for a free Shopify Partner account: https://partners.shopify.com/signup
2. From the Partner Dashboard → **Stores** → **Add store** → **Create development store**.
   - Store type: **Create a store to test and build**
   - Build a store for: **A new client**
   - Store name: `ai-retail-os-demo` (any value; the dev store URL becomes `<store-name>.myshopify.com`)
   - Plan: **Developer Preview** (default — gives you all Shopify Plus features for free during build)
3. Inside the new store admin → **Settings** → **Apps and sales channels** → **Develop apps** → **Allow custom app development**, then **Create an app** named `AI Retail OS`.
4. Configure Admin API access scopes (Configuration tab → Admin API integration → Configure):

   | Scope | Why |
   |---|---|
   | `read_products`, `write_products` | P3 sync + P4 metafield round-trip |
   | `read_orders`, `write_orders` | P3 order sync + P4 fulfillment routing |
   | `read_inventory`, `write_inventory` | P3 inventory levels + P2 seed |
   | `read_locations` | P3 location sync |
   | `write_discounts` | P4 `promotion` apply (`discountAutomaticBasicCreate`) |
   | `read_marketing_events`, `write_marketing_events` | P4 `campaign_brief` apply (`marketingActivityCreate`) |
   | `read_fulfillments`, `write_fulfillments` | P4 `fulfillment_routing` apply (`fulfillmentOrderMove`) |

5. Click **Install app** → **API credentials** tab → reveal the **Admin API access token** (`shpat_…`). This is your `SHOPIFY_ADMIN_TOKEN`.
6. Drop the env block into `backend/.env` (root `.env` works too — `app.config` reads root first):

   ```bash
   SHOPIFY_SHOP_DOMAIN=ai-retail-os-demo.myshopify.com
   SHOPIFY_ADMIN_TOKEN=shpat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   SHOPIFY_API_VERSION=2025-01
   ```

7. Optional Marketing passthrough — if you also want `campaign_brief` to land in **Klaviyo** instead of Shopify Email:

   ```bash
   KLAVIYO_API_KEY=pk_xxxxxxxxxxxxxxxx
   ```

8. Sanity-check the credentials before running the seed:

   ```bash
   curl -sS \
     -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_TOKEN" \
     "https://$SHOPIFY_SHOP_DOMAIN/admin/api/$SHOPIFY_API_VERSION/shop.json" | head
   ```

   A `200` with the shop JSON means scopes + token are good.

## Why no Docker

Shopify hosts everything (Admin, storefront, GraphQL endpoint). There is no self-host path — Shopify Plus is the product. The Partner dev store is free indefinitely while in development; it becomes a paid plan only when you transfer it to a merchant.

## Lifecycle

| Make target | What it does |
|---|---|
| `make shopify-seed` | projects demo data from `backend/data/spine.db` into the dev store (30 Products + 5 Locations + inventory levels + 10 draft Orders). Idempotent via `metafield.namespace=retail_os` markers — re-runs print zero `++` lines |

No `up`/`down`/`bootstrap`/`status`/`logs`/`nuke` — Shopify owns the runtime.

## API surface the cockpit hits

The adapter (Track 1 P3+, mirrored for Shopify in Track 8) talks to two Admin API surfaces:

- **REST Admin API** — `GET/POST /admin/api/{version}/{resource}.json` — used for the seed (simpler call shape) and the `shop.json` health check.
- **GraphQL Admin API** — `POST /admin/api/{version}/graphql.json` — used by `_live_sync` (cursor pagination via `pageInfo.endCursor`) and `apply_outbound` mutations (`discountAutomaticBasicCreate`, `fulfillmentOrderMove`, `marketingActivityCreate`).

Both share the same `X-Shopify-Access-Token: shpat_…` header.

## Reset path

Two paths depending on what you need:

| What needs reset | How |
|---|---|
| Just the seeded demo data | Re-create the dev store (Partner Dashboard → store → Settings → **Delete store**, then repeat the Quick start). Shopify has no bulk-delete API for products + orders + locations together; deleting + re-creating the store is faster than scripting cleanup. |
| Just the API token | Custom apps tab → uninstall the `AI Retail OS` app → reinstall → reveal new token → update `.env`. Old token is invalidated immediately. |
| All of it | Delete the dev store (preserves your Partner account + future store quota). Provision a fresh one from step 2 above. |

## What is intentionally out of scope

- **Storefront API / Hydrogen / Liquid theme work** — the cockpit only talks to Admin API. The dev store's default theme is fine for the P5 walkthrough screenshots.
- **Customers + segmentation** — Shopify Customers exist but the seed skips them. The cockpit's customer-segment surface lives in Mautic; cross-system identity stitching is a separate piece of work, not part of Track 8.
- **Multi-currency + markets** — single-currency (USD) only. Shopify Markets is gated by plan and complicates the seed.
- **Webhooks** — no webhook subscriptions yet; Shopify outbound events are pulled on demand via `sync_inbound`. Webhooks land in a follow-up if push-driven cockpit refresh becomes needed.

## Apple Silicon notes

N/A — nothing runs locally. The dev store is in Shopify's cloud.
