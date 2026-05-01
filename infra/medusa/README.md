# Medusa stack — local dev/demo

Single-host docker compose for running [MedusaJS v2](https://docs.medusajs.com/) alongside the cockpit. Same shape as `infra/erpnext/` and `infra/mautic/`. **Demo only** — no TLS, no SMTP, single admin account.

## Quick start

```bash
make medusa-up         # builds the image (first run is slow), starts db + redis + medusa
make medusa-bootstrap  # db:migrate + create admin user (idempotent)
```

The bootstrap script prints the admin URL, credentials, and the three env vars to drop into `backend/.env` so the cockpit's Medusa adapter can flip out of mock mode in P3.

| Endpoint | Where |
|---|---|
| Server | `http://localhost:9000` |
| Admin UI | `http://localhost:9000/app` |
| Admin | `admin@retail.local / retail-medusa` (override with `MEDUSA_ADMIN_PASSWORD`) |

## Why the custom image

Medusa doesn't ship an official Docker image — the upstream-recommended path is to bake your own off `node:22-alpine` and clone the `medusa-starter-default` template at build time. The `Dockerfile` in this directory does exactly that:

- Pinned to `node:22-alpine` (Medusa v2 needs Node 20+; 22 is the LTS upstream targets).
- Clones `medusajs/medusa-starter-default` shallowly + `yarn install --frozen-lockfile` so the image is reproducible.
- Build args `MEDUSA_STARTER_REPO` / `MEDUSA_STARTER_REF` let you track a fork or pin a specific tag.

`postgres:15-alpine` and `redis:7-alpine` are the upstream-blessed backing services for v2. Both ship multi-arch and run cleanly on Apple Silicon.

## Lifecycle

| Make target | What it does |
|---|---|
| `make medusa-up` | builds the medusa image (first run is ~3–5 min) and `docker compose up -d` (postgres + redis + medusa) |
| `make medusa-bootstrap` | `npx medusa db:migrate` + `npx medusa user` (both idempotent) — prints API + admin creds |
| `make medusa-seed` | projects demo data from `backend/data/spine.db`: 1 Sales Channel, 5 Stock Locations (one per substrate store), 30 Products (one per SKU, single variant, USD pricing). Idempotent — re-runs print zero `++` lines |
| `make medusa-status` | `docker compose ps` |
| `make medusa-logs` | tails the three service logs |
| `make medusa-down` | stops the stack, **keeps volumes** (db + uploaded media survives) |
| `make medusa-nuke` | stops + wipes all volumes (full reset) |

## API surface we'll use

Medusa v2 splits the API into `/store/*` (storefront) and `/admin/*` (back-office). The cockpit's adapter (Track 1 P3+) will hit at minimum:

- `GET  /admin/orders` — inbound sync (mirror live ecom orders into substrate)
- `GET  /admin/inventory-items` + `/admin/stock-locations` — inbound sync (online stock)
- `GET  /admin/sales-channels` — inbound sync (channel definitions)
- `POST /admin/orders/{id}/fulfillments` — outbound apply (`fulfillment_routing`)
- `POST /admin/inventory-items/{id}/location-levels` — outbound apply (`store_transfer` reservation)

## Reset

```bash
make medusa-nuke && make medusa-up && make medusa-bootstrap
```

That re-builds the image, recreates postgres, re-runs migrations, and re-creates the admin user. The `medusa db:migrate` command is idempotent on its own, but a clean nuke is the only way to recover from a corrupted starter clone or a partially-applied migration.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| First `make medusa-up` takes a long time | Image build clones medusa-starter and runs yarn install. ~3–5 min on a fresh machine. Check `make medusa-logs`. |
| `medusa: not found` in bootstrap | Medusa v2 ships the CLI as `npx medusa` (not a global binary). The bootstrap script already uses `npx medusa …`. |
| `db:migrate` fails | Postgres healthcheck didn't fire yet. Re-run `make medusa-bootstrap` — migrations are idempotent. |
| `medusa user` exits non-zero on re-run | Expected — Medusa CLI returns non-zero when the email already exists. Bootstrap absorbs that and continues. |
| Server starts but `/health` returns 502 | Medusa is still loading modules. First boot can take 30s; check logs with `make medusa-logs`. |
| Apple Silicon: image won't build | The Dockerfile uses `node:22-alpine` (multi-arch). If a transitive dep needs glibc, switch to `node:22` (Debian-based) — heavier but compatible. |
| Want a clean slate | `make medusa-nuke` (wipes db + uploads). Then `make medusa-up && make medusa-bootstrap`. |

## Out of scope here

- Inbound sync (Track 1 Medusa P3 — flip the existing `MedusaAdapter` out of mock once `MEDUSA_*` env is set)
- Outbound apply (Track 1 Medusa P4 — `fulfillment_routing` → `POST /admin/orders/{id}/fulfillments`; `store_transfer` → reservation)
- Inventory levels per (variant × stock_location) — left for P3 once both sides exist
- Orders / customers / regions seed — Medusa v2 makes these region+cart-bound, deferred to P3 too
- TLS / multi-tenant / production hardening — never for this stack
