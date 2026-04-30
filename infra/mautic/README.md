# Mautic stack — local dev/demo

Single-host docker compose for running Mautic 5 alongside the cockpit. Same shape as `infra/erpnext/`. **Demo only** — no TLS, no SMTP (mail goes to the `null` DSN), single admin account.

## Quick start

```bash
make mautic-up         # starts mariadb + mautic_web + mautic_cron + mautic_worker
make mautic-bootstrap  # one-shot install + admin user + API basic-auth toggle
```

The bootstrap script prints the admin URL, credentials, and the three env vars to drop into `backend/.env` so the cockpit's Mautic adapter flips out of mock mode.

| Endpoint | Where |
|---|---|
| Web UI | `http://localhost:8081` |
| API base | `http://localhost:8081/api` |
| Admin | `admin / retail-mautic` (override with `MAUTIC_ADMIN_PASSWORD`) |

## Why these images

- **`mautic/mautic:5-apache`** is the upstream-blessed apache variant of Mautic 5. The image is multi-arch (amd64 + arm64) so it runs on Apple Silicon. We pin the index digest of the floating tag (`sha256:66974d1c…`) so re-pulls are reproducible without choosing a per-build daily tag that rotates fast.
- **`mariadb:10.6`** matches the ERPNext stack — keeps the local mental model the same.
- **No redis** — Mautic 5 doesn't require it; we let it default to file-cache. Fine for a single-tenant demo.

## Lifecycle

| Make target | What it does |
|---|---|
| `make mautic-up` | `docker compose up -d` (db + web + cron + worker) |
| `make mautic-bootstrap` | runs `bin/console mautic:install` (idempotent), enables API basic auth, prints API creds |
| `make mautic-status` | `docker compose ps` |
| `make mautic-logs` | tails the four service logs |
| `make mautic-down` | stops the stack, **keeps volumes** (db + uploaded media survives) |
| `make mautic-nuke` | stops + wipes all volumes (full reset) |

## API surface we'll use

Mautic 5 exposes everything under `/api/`. The cockpit's adapter (Track 1 P3+) will hit at minimum:

- `GET  /api/contacts` — inbound sync (mirror customer base)
- `GET  /api/segments` — inbound sync (audience definitions)
- `GET  /api/campaigns` — inbound sync (live campaigns + stats)
- `POST /api/contacts/new` — outbound apply (create contact)
- `POST /api/segments/new` — outbound apply (create segment for a category push)
- `POST /api/campaigns/new` — outbound apply (draft campaign skeleton)

Webhook for measurement (already in main): `POST /api/integrations/mautic/webhook` — Mautic's webhook config in the UI points at this URL.

## Reset

```bash
make mautic-nuke && make mautic-up && make mautic-bootstrap
```

That's the only reliable way to re-run install — Mautic's `mautic:install` refuses if `config/local.php` already exists.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `bootstrap.sh` hangs at "waiting for mautic_web" | first pull is ~700 MB; check `make mautic-logs` |
| `mautic:install` fails on db | mariadb isn't healthy yet — re-run bootstrap, the install is idempotent |
| `/api/contacts` returns 401 | bootstrap didn't flip the API basic-auth toggle. Re-run bootstrap; the script always re-applies the toggle |
| Apple Silicon: image won't pull | upstream daily tag occasionally drops arm64. The pinned `5-apache` index digest in `docker-compose.yml` is multi-arch — keep the digest pinned |
| Want a clean slate | `make mautic-nuke` (wipes db + media). Then `make mautic-up && make mautic-bootstrap` |

## Out of scope here

- Outbound sync logic (Track 1 Mautic P3+)
- Demo data seed (Track 1 Mautic P2 — separate `seed.py` like ERPNext)
- TLS / multi-tenant / production hardening — never for this stack
