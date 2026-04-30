# Local ERPNext for the AI Retail OS demo

A minimal, single-host ERPNext v15 stack used to back the cockpit's `erpnext` integration adapter. **Demo only** — no TLS, no clustering, no backups.

## What's in here

| File | Purpose |
|---|---|
| `docker-compose.yml` | mariadb + redis (cache + queue) + frappe (configurator + backend + 2 workers + scheduler + websocket) + nginx frontend on :8080 |
| `bootstrap.sh` | One-shot site creation + ERPNext app install + Administrator API-key generation |
| `README.md` | This file |

## Quick start

```bash
make erpnext-up         # start the stack (~1–2 min on first run while images pull)
make erpnext-bootstrap  # create site retail.localhost, install ERPNext, mint API key + secret
```

The bootstrap script prints the API key + secret as its last line. Copy them into `backend/.env`:

```env
ERPNEXT_BASE_URL=http://localhost:8080
ERPNEXT_API_KEY=<from bootstrap output>
ERPNEXT_API_SECRET=<from bootstrap output>
ERPNEXT_COMPANY=AI Retail OS
```

Sanity check:

```bash
curl -s http://localhost:8080/api/method/frappe.auth.get_logged_user \
  -H "Authorization: token <ERPNEXT_API_KEY>:<ERPNEXT_API_SECRET>"
# → {"message":"Administrator"}
```

UI: <http://localhost:8080> · login `Administrator` / `retail-admin` (or `ERPNEXT_ADMIN_PASSWORD` if you set it).

## Stop / reset

```bash
make erpnext-down   # stop, keep volumes (data and site survive)
make erpnext-nuke   # stop and wipe volumes — full clean slate
make erpnext-logs   # tail aggregate stack logs
make erpnext-status # docker compose ps
```

## Why we run it this way

- **Pinned to a multi-arch v15 digest** — Frappe ships per-version tags (e.g. `v15.45.0`) as amd64-only, which breaks on Apple Silicon. We pin the digest of the floating `v15` tag (which has both linux/amd64 and linux/arm64 manifests) so re-pulls are reproducible without sacrificing arm64. Bump the digest deliberately when you want a newer release.
- **One site (`retail.localhost`)** — Frappe is multi-tenant; we don't need that here. The `FRAPPE_SITE_NAME_HEADER` in nginx makes `localhost:8080` resolve to that site without DNS games.
- **`Administrator` user, API key + secret** — the simplest auth path. The adapter sends `Authorization: token <key>:<secret>` to every Frappe REST call.
- **MariaDB 10.6 + Redis 6.2** — versions Frappe v15 tests against.

## Common gotchas

| Symptom | Fix |
|---|---|
| `ERR_CONNECTION_REFUSED` on :8080 | `make erpnext-status` — the `frontend` container should be `Up`. If `configurator` keeps exiting, check `make erpnext-logs` for db connection errors. |
| `bench new-site` complains about MariaDB charset | mariadb image flag handles it (`--character-set-server=utf8mb4`); only triggers if you've replaced the image. |
| Want to re-bootstrap from scratch | `make erpnext-nuke && make erpnext-up && make erpnext-bootstrap`. |
| Need a different port | Edit the `frontend.ports` mapping in `docker-compose.yml`. Note that `ERPNEXT_BASE_URL` must match. |
| Apple Silicon image issues | `frappe/erpnext:v15.x` ships multi-arch; if a sub-image (e.g. `mariadb`) lags, set `--platform linux/amd64` on the affected service. |

## Out of scope

- Production hardening (TLS, replication, backup volumes, secret management). This stack is for local development and demo runs only — do not expose it publicly.
- Email transports, SMS, payment-gateway plugins, or any other ERPNext extras beyond what `erpnext` itself installs.
