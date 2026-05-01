# Akeneo PIM stack — local dev/demo

Single-host docker compose for running Akeneo Community Edition 7.x alongside the cockpit. Same shape as the other `infra/<system>/` rollouts. **Demo only** — no TLS, no SMTP, single admin account.

> **Heads up:** Akeneo CE's Docker setup is a moving target across versions. The files here are *demo scaffolding* and clone `akeneo/pim-community-standard` at image build time pinned to `7.0`. If your minor differs, override the build args (`AKENEO_REF`) or point the compose at your own pim-community-standard checkout. The cockpit's adapter is version-tolerant — it talks to the documented `/api/rest/v1/*` surface.

## Quick start

```bash
make akeneo-up         # builds the akeneo image (composer install ~5-10 min)
make akeneo-bootstrap  # pim:installer:db + admin user + OAuth2 client
```

The bootstrap script prints the admin URL, default credentials, and the env block to drop into `backend/.env`.

| Endpoint | Where |
|---|---|
| Web UI | `http://localhost:8083` |
| API base | `http://localhost:8083/api/rest/v1` |
| Token endpoint | `http://localhost:8083/api/oauth/v1/token` |
| Admin | `admin / retail-akeneo` |

> **Apple Silicon:** the upstream `php:8.1-apache` base is multi-arch but Akeneo's composer dependencies pull a few amd64-only packages. The compose pins `platform: linux/amd64` so it boots under emulation. Slower than ERPNext / Medusa.

## Why the custom image

Akeneo doesn't ship an all-in-one Docker Hub image — the upstream-recommended path for CE is to clone `akeneo/pim-community-standard` and run their dev compose. The Dockerfile inverts that: clones the repo + composer-installs at build time so the operator only touches `make akeneo-*`.

The stack:
- **`mysql:8.0`** — Akeneo CE 7+ requires 8.0 (changesets target this dialect).
- **`opensearchproject/opensearch:2.11.1`** — Akeneo's drop-in replacement for Elasticsearch 7. Single-node, security plugin disabled for the demo.
- **`akeneo`** — custom Apache+PHP container. Composer-installed at build, runs `pim:installer:db` + `pim:user:create` + `pim:oauth-server:create-client` at bootstrap time.

## Lifecycle

| Make target | What it does |
|---|---|
| `make akeneo-up` | builds the akeneo image (first run is ~5-10 min: clone + composer install) and `docker compose up -d` |
| `make akeneo-bootstrap` | runs `pim:installer:db` + `pim:user:create` + `pim:oauth-server:create-client` (all idempotent — re-runs are clean) |
| `make akeneo-seed` | projects demo data: 3 Categories + 30 Products. Idempotent — re-runs print zero `++` lines |
| `make akeneo-status` | `docker compose ps` |
| `make akeneo-logs` | tails the three service logs |
| `make akeneo-down` | stops the stack, **keeps volumes** (mysql + opensearch + uploads survive) |
| `make akeneo-nuke` | stops + wipes all volumes (full reset) |

## API surface we'll use

Akeneo's REST API uses OAuth2 with both client credentials (Basic auth) and a user grant_type=password layer. The cockpit's adapter (Track 1 P3+) hits at minimum:

- `POST /api/oauth/v1/token` — token round-trip
- `GET  /api/rest/v1/categories` — inbound sync (mirror catalogue tree)
- `GET  /api/rest/v1/products` — inbound sync (mirror enriched catalog)
- `PATCH /api/rest/v1/products/{code}` — outbound apply (`pim_enrich`; merge semantics, fields not in the body stay untouched)

## Reset

```bash
make akeneo-nuke && make akeneo-up && make akeneo-bootstrap && make akeneo-seed
```

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| First `make akeneo-up` takes a long time | Composer install runs against ~3000 packages. ~5-10 min on a fresh machine. `make akeneo-logs` to watch progress. |
| `pim:installer:db` fails on first run | OpenSearch isn't healthy yet. Re-run `make akeneo-bootstrap` — `pim:installer:db` is idempotent. |
| `pim:oauth-server:create-client` doesn't print client_id / secret | Output format varies across CE minors. Run it manually: `docker compose exec akeneo bin/console pim:oauth-server:create-client retail-os --grant_type=password --grant_type=refresh_token`. |
| `/api/oauth/v1/token` returns 400 | The cockpit needs both Basic auth (client_id:secret) AND the `grant_type=password` body with the admin user/pw. Missing either fails. |
| `/api/rest/v1/*` returns 401 mid-session | The cockpit caches the bearer. On 401 the adapter re-logins and retries once — verify creds if the second attempt also fails. |
| Apple Silicon: container runs slowly | Expected — the WAR / composer deps include amd64-only modules; runs under qemu emulation. |
| Want a clean slate | `make akeneo-nuke` (wipes mysql + opensearch + uploads). Then `make akeneo-up && make akeneo-bootstrap && make akeneo-seed`. |

## Out of scope here

- TLS / multi-tenant / production hardening — never for this stack
- Asset / media upload pipeline — Akeneo's Asset Manager is a separate sub-app
- Multi-locale enrichment — the seed only fills `en_US`
- Custom Family / AttributeOption seeding — uses the bundled `default` family
