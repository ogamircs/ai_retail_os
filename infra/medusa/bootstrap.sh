#!/usr/bin/env bash
# One-shot bootstrap for the demo Medusa instance.
# Idempotent: safe to re-run — existing migrations and users are detected.
#
# What it does:
#   1. Brings the stack up (docker compose up -d, builds the image first).
#   2. Waits for postgres to be healthy.
#   3. Runs `medusa db:migrate` (idempotent — Medusa skips applied rows).
#   4. Creates the admin user via `medusa user` (skipped if exists).
#   5. Prints API base URL + admin creds + the env block to paste into
#      backend/.env.

set -euo pipefail

ADMIN_EMAIL="${MEDUSA_ADMIN_EMAIL:-admin@retail.local}"
ADMIN_PASSWORD="${MEDUSA_ADMIN_PASSWORD:-retail-medusa}"
DB_USER="${MEDUSA_DB_USER:-medusa}"
DB_PASSWORD="${MEDUSA_DB_PASSWORD:-retail-db}"
DB_NAME="${MEDUSA_DB_NAME:-medusa}"
SITE_URL="${MEDUSA_SITE_URL:-http://localhost:9000}"

cd "$(dirname "$0")"

COMPOSE="docker compose -p ai-retail-medusa -f docker-compose.yml"

echo ">> ensuring stack is up (builds the medusa image on first run)"
$COMPOSE up -d

echo ">> waiting for postgres"
# Pass the password via env so the readiness check authenticates as the
# medusa app user with its own password — same lesson as the Mautic
# bootstrap fix.
for _ in $(seq 1 60); do
  if $COMPOSE exec -T -e PGPASSWORD="${DB_PASSWORD}" db \
      pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ">> waiting for the medusa container to be ready"
for _ in $(seq 1 90); do
  if $COMPOSE exec -T medusa test -f /app/package.json >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ">> running db:migrate (idempotent)"
$COMPOSE exec -T medusa npx medusa db:migrate

# Medusa v2 user-creation: `npx medusa user --email X --password Y`.
# Exits non-zero when the email already exists — that case is fine to
# swallow on re-runs. Any *other* non-zero (DB down, auth misconfig,
# bad password policy) must abort the bootstrap so operators don't end
# up with printed creds that don't actually work.
echo ">> ensuring admin user (idempotent)"
set +e
user_output=$(
  $COMPOSE exec -T \
    -e BS_ADMIN_EMAIL="$ADMIN_EMAIL" \
    -e BS_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    medusa bash -c 'npx medusa user --email "$BS_ADMIN_EMAIL" --password "$BS_ADMIN_PASSWORD"' 2>&1
)
user_rc=$?
set -e
if [ $user_rc -eq 0 ]; then
  echo "++ admin user created"
elif echo "$user_output" | grep -qiE "already exists|duplicate key|unique constraint|email is already"; then
  echo "   admin user already exists (continuing)"
else
  echo "$user_output"
  echo "!! medusa user creation failed (exit=$user_rc) — bootstrap aborted."
  echo "   Fix the underlying error (DB / config / password policy) and re-run."
  exit "$user_rc"
fi

cat <<BANNER

======================================================================
Medusa is up at:    ${SITE_URL}
Admin UI:           ${SITE_URL}/app
Admin email:        ${ADMIN_EMAIL}
Admin password:     ${ADMIN_PASSWORD}

API credentials — paste into backend/.env:
----------------------------------------------------------------------
MEDUSA_BASE_URL=${SITE_URL}
MEDUSA_ADMIN_EMAIL=${ADMIN_EMAIL}
MEDUSA_ADMIN_PASSWORD=${ADMIN_PASSWORD}
----------------------------------------------------------------------

Sanity check (health endpoint):
  curl -s '${SITE_URL}/health'
  # → "OK"

Reset path: \`make medusa-nuke && make medusa-up && make medusa-bootstrap\`.
NOTE: First boot is slow — the docker build clones the medusa-starter
repo and runs yarn install (~3-5 min on a fresh machine).
======================================================================
BANNER
