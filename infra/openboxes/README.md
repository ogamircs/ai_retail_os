# OpenBoxes stack — local dev/demo

Single-host docker compose for running OpenBoxes 0.9.x alongside the cockpit. Same shape as `infra/erpnext/`, `infra/mautic/`, `infra/medusa/`. **Demo only** — no TLS, no SMTP, single admin account.

## Quick start

```bash
make openboxes-up         # builds the openboxes image (first run pulls
                          #   the WAR ~190MB), starts mysql + tomcat
make openboxes-bootstrap  # waits for Liquibase migrations on first boot
                          #   (3-5 min on a fresh machine)
```

The bootstrap script prints the admin URL, default credentials, and the env block to drop into `backend/.env` once you've signed in once and minted an API token via the OpenBoxes UI.

| Endpoint | Where |
|---|---|
| Web UI | `http://localhost:8082` |
| API base | `http://localhost:8082/api` |
| Admin | `openboxes / password` (you'll be prompted to change on first login) |

> **Apple Silicon note:** OpenBoxes 0.9.x WAR is amd64-only. The compose file pins `platform: linux/amd64` so it runs (under emulation) on M-series Macs. Slow but functional.

## Why the custom image

OpenBoxes doesn't ship an official Docker Hub image. The upstream-recommended path is a Tomcat container with the WAR dropped in. The Dockerfile does that:

- `tomcat:9-jdk17` base — matches OpenBoxes 0.9.x runtime requirements.
- WAR pulled at build time from the GitHub release (`v0.9.7-hotfix1` by default).
- `--build-arg OPENBOXES_VERSION=…` lets you track a newer release without forking.

`mysql:5.7` is the upstream-blessed backing store — OpenBoxes' Liquibase changesets target MySQL 5.7's exact SQL dialect.

## Lifecycle

| Make target | What it does |
|---|---|
| `make openboxes-up` | renders `openboxes-config.properties` from the template (substitutes `OPENBOXES_DB_*` env values), builds the image (first run is ~5 min) and `docker compose up -d` (mysql + tomcat) |
| `make openboxes-bootstrap` | waits for Liquibase migrations (no command to run by hand — Grails handles them on first Tomcat boot), prints admin creds + env block |
| `make openboxes-seed` | projects spine demo data (5 Locations + 30 Products). Idempotent — re-runs print zero `++` lines |
| `make openboxes-status` | `docker compose ps` |
| `make openboxes-logs` | tails the two service logs |
| `make openboxes-down` | stops the stack, **keeps volumes** (mysql data + uploads survive) |
| `make openboxes-nuke` | stops + wipes all volumes (full reset) |

## API surface we'll use

OpenBoxes exposes a JSON API under `/api/*`. The cockpit's adapter (Track 1 P3+) hits at minimum:

- `POST /api/login` — token auth
- `GET  /api/locations` — inbound sync (mirror sites/depots)
- `GET  /api/products` — inbound sync (mirror catalogue)
- `GET  /api/shipments?direction=INBOUND` — inbound sync (mirror PO state)
- `POST /api/shipments/{id}/comments` — outbound apply (`po_held` / `po_expedited` annotation)

## Reset

```bash
make openboxes-nuke && make openboxes-up && make openboxes-bootstrap
```

That re-builds the image, recreates mysql, lets Liquibase re-migrate, and seeds the default admin user. Liquibase migrations on a fresh DB take 3-5 minutes the first time.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| First `make openboxes-up` takes a long time | Image build downloads the ~190 MB WAR + Liquibase applies 200+ changesets on first boot. `make openboxes-logs` to watch progress. |
| `make openboxes-bootstrap` times out at the Tomcat readiness loop | First-boot Liquibase isn't done yet. Check `make openboxes-logs`; re-running the bootstrap is safe. |
| Apple Silicon: container runs slowly | Expected — OpenBoxes 0.9.x is amd64-only and runs under qemu emulation. The compose file keeps `platform: linux/amd64` so it boots, but expect 2-3× slower than ERPNext. |
| `/api/login` returns 401 with the default creds | OpenBoxes prompts for a password change on first login. Sign in once via `/`, change the password, then update `OPENBOXES_PASSWORD` in `backend/.env`. |
| WAR download fails | The GitHub release asset URL is pinned in the Dockerfile build arg `OPENBOXES_WAR_URL`. Override at build time if you're hitting a network restriction. |
| Want a clean slate | `make openboxes-nuke` (wipes mysql + uploads). Then `make openboxes-up && make openboxes-bootstrap`. |

## Out of scope here

- TLS / multi-tenant / production hardening — never for this stack
- Non-Depot location types (Vendor, Supplier, Buyer) — the seed only creates the demo retail Depots; OpenBoxes' Vendor/Supplier domains are richer than substrate exposes
- Stock-on-hand levels — `infra/openboxes/seed.py` only handles Locations + Products; substrate-driven stock movement is covered in P3+
