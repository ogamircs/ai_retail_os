# Apache Superset (demo)

Single-host Superset for the AI Retail OS demo. Same shape as the
other `infra/*` stacks — postgres metadata + redis cache/broker + the
official `apache/superset:3.1.1` web image.

The cockpit's `spine.db` is bind-mounted **read-only** at
`/spine/spine.db` inside the Superset container so the demo dashboard
can register it as a SQLAlchemy database connection without copying
data around. Superset stays read-only over the spine — this matches
Superset's role in the architecture (BI surface, not a system of
record).

## Quick start

```bash
make superset-up         # starts postgres + redis + superset on :8088
make superset-bootstrap  # superset db upgrade + admin user + roles
make superset-seed       # registers spine.db + creates datasets/charts/dashboard
```

Sign in at <http://localhost:8088> with the credentials printed by
`bootstrap.sh` (default: `admin` / `retail-superset`).

`backend/.env`:

```env
SUPERSET_BASE_URL=http://localhost:8088
SUPERSET_USERNAME=admin
SUPERSET_PASSWORD=retail-superset
```

## Lifecycle

| Target | What it does |
| --- | --- |
| `superset-up` | `docker compose up -d` (no build — uses the official 3.1.1 image) |
| `superset-bootstrap` | waits for db/redis, runs `superset db upgrade`, ensures admin user, runs `superset init` |
| `superset-seed` | creates DB connection over the bind-mounted `spine.db`, registers 3 datasets, builds 3 charts + 1 dashboard |
| `superset-status` | `docker compose ps` |
| `superset-logs` | tail compose logs |
| `superset-down` | stop, keep volumes |
| `superset-nuke` | stop and wipe volumes (`docker compose down -v`) |

## REST surface used by the cockpit

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/security/login` | mint a short-lived JWT (`access_token`) |
| `GET /api/v1/database/?q=...` | list registered databases (paginated) |
| `GET /api/v1/dataset/?q=...` | list datasets (the spine tables we registered) |
| `GET /api/v1/dashboard/?q=...` | list dashboards (we cache one per row) |
| `GET /api/v1/chart/?q=...` | list charts |

The adapter caches each row into `record_cache` + `external_refs` and
deep-links the cockpit drawer at `/superset/dashboard/<id>`.

## Troubleshooting

- **`superset db upgrade` hangs on first boot**: postgres healthcheck
  returns ready before the connection pool is fully online. The bootstrap
  script polls `pg_isready` and retries; if the upgrade still hangs,
  `make superset-down && make superset-up` re-binds.
- **Admin login returns 401**: check `SUPERSET_SECRET_KEY` matches what
  `superset init` was run with — changing the secret invalidates session
  cookies. Easiest fix: `make superset-nuke && make superset-up && make superset-bootstrap`.
- **Sanity curl returns `{"msg": "Bad username and password"}`**: the
  admin password env var didn't propagate. Re-run `make superset-bootstrap`
  with `SUPERSET_ADMIN_PASSWORD=…` set in your shell.
- **Apple Silicon**: `apache/superset:3.1.1` is a multi-arch image —
  no platform pin needed.
- **`spine.db` access denied inside the container**: the bind mount is
  read-only and lives at `/spine/spine.db`. The cockpit's seed registers
  `sqlite:////spine/spine.db` (note the four slashes) as the SQLAlchemy
  URI; if you run a non-root user inside the container, the host file's
  permissions need to allow other-read.

## Reset path

```bash
make superset-nuke && make superset-up && make superset-bootstrap && make superset-seed
```

This wipes the metadata database and admin user; the spine.db file is
untouched.
