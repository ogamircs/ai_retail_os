#!/usr/bin/env bash
# One-shot bootstrap for the demo Akeneo PIM instance.
#
# Akeneo CE installs the schema + minimal demo catalog via
# `bin/console pim:installer:db` (idempotent — re-runs are no-ops once
# the schema exists). After that we mint an OAuth2 client + admin
# user via `bin/console pim:user:create` so the cockpit's adapter
# can authenticate against /api/oauth/v1/token.

set -euo pipefail

ADMIN_USER="${AKENEO_ADMIN_USER:-admin}"
ADMIN_PASSWORD="${AKENEO_ADMIN_PASSWORD:-retail-akeneo}"
ADMIN_EMAIL="${AKENEO_ADMIN_EMAIL:-admin@retail.local}"
CLIENT_LABEL="${AKENEO_CLIENT_LABEL:-retail-os}"
DB_USER="${AKENEO_DB_USER:-akeneo}"
DB_PASSWORD="${AKENEO_DB_PASSWORD:-akeneo}"
DB_NAME="${AKENEO_DB_NAME:-akeneo_pim}"
SITE_URL="${AKENEO_SITE_URL:-http://localhost:8083}"

cd "$(dirname "$0")"

COMPOSE="docker compose -p ai-retail-akeneo -f docker-compose.yml"

echo ">> ensuring stack is up (builds the akeneo image on first run; ~5-10 min)"
$COMPOSE up -d

echo ">> waiting for mysql"
for _ in $(seq 1 90); do
  if $COMPOSE exec -T -e MYSQL_PWD="${DB_PASSWORD}" db \
      mysqladmin ping -h localhost -u "${DB_USER}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ">> waiting for opensearch"
for _ in $(seq 1 60); do
  if $COMPOSE exec -T opensearch \
      curl -fsS http://localhost:9200/_cluster/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ">> waiting for akeneo container readiness"
for _ in $(seq 1 90); do
  if $COMPOSE exec -T akeneo test -f /srv/pim/bin/console >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ">> running pim:installer:db"
# Akeneo's installer is idempotent in spirit (it can be re-run on an
# already-installed database) but it surfaces "already installed"
# conditions as non-zero exits with predictable messages. Capture
# stderr + the exit code; swallow ONLY when the message matches a
# known benign signal. Anything else (MySQL still unavailable,
# OpenSearch unhealthy, command shape changed, fixture path missing)
# must abort the bootstrap rather than print usable-looking creds
# against a partially-initialised instance.
set +e
installer_output=$(
  $COMPOSE exec -T akeneo \
    bin/console pim:installer:db --env=prod \
      --catalog=src/PimCommunity/Bundle/InstallerBundle/Resources/fixtures/minimal 2>&1
)
installer_rc=$?
set -e
if [ $installer_rc -eq 0 ]; then
  echo "++ pim:installer:db completed"
elif echo "$installer_output" | grep -qiE "already (installed|loaded|exist|present)|schema is already|tables already|database (is )?not empty|nothing to install"; then
  echo "   pim:installer:db reports already-installed (continuing)"
else
  echo "$installer_output"
  echo "!! pim:installer:db failed (exit=$installer_rc) — bootstrap aborted."
  echo "   Fix the underlying error (mysql/opensearch readiness, fixture"
  echo "   path, or command shape change) and re-run."
  exit "$installer_rc"
fi

echo ">> ensuring admin user (idempotent)"
set +e
user_output=$(
  $COMPOSE exec -T \
    -e BS_ADMIN_USER="$ADMIN_USER" \
    -e BS_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    -e BS_ADMIN_EMAIL="$ADMIN_EMAIL" \
    akeneo bash -c '
      bin/console pim:user:create \
        "$BS_ADMIN_USER" \
        "$BS_ADMIN_PASSWORD" \
        "$BS_ADMIN_EMAIL" \
        Admin User en_US --admin --no-interaction
    ' 2>&1
)
user_rc=$?
set -e
if [ $user_rc -eq 0 ]; then
  echo "++ admin user created"
elif echo "$user_output" | grep -qiE "already exists|duplicate|unique"; then
  echo "   admin user already exists (continuing)"
else
  echo "$user_output"
  echo "!! akeneo user creation failed (exit=$user_rc) — bootstrap aborted."
  exit "$user_rc"
fi

echo ">> minting OAuth2 client"
set +e
client_output=$(
  $COMPOSE exec -T \
    -e BS_CLIENT_LABEL="$CLIENT_LABEL" \
    akeneo bash -c '
      bin/console pim:oauth-server:create-client "$BS_CLIENT_LABEL" \
        --grant_type=password --grant_type=refresh_token --no-interaction
    ' 2>&1
)
client_rc=$?
set -e
if [ $client_rc -eq 0 ]; then
  echo "++ OAuth2 client created"
elif echo "$client_output" | grep -qiE "already exists|duplicate|unique constraint|label.*used"; then
  # CE prints something like "Client with label 'retail-os' already exists"
  # on a re-run — that's fine; the operator can re-list existing clients.
  echo "   OAuth2 client '$CLIENT_LABEL' already exists (continuing; re-list with"
  echo "   \`bin/console pim:oauth-server:list-clients\` to recover client_id/secret)"
else
  echo "$client_output"
  echo "!! pim:oauth-server:create-client failed (exit=$client_rc) — bootstrap aborted."
  echo "   Fix the underlying error (service not ready / command shape change)"
  echo "   and re-run."
  exit "$client_rc"
fi
client_id=$(echo "$client_output" | grep -oE 'client_id:\s*\S+' | awk -F: '{print $2}' | xargs || true)
client_secret=$(echo "$client_output" | grep -oE 'secret:\s*\S+' | awk -F: '{print $2}' | xargs || true)

cat <<BANNER

======================================================================
Akeneo PIM is up at: ${SITE_URL}
Admin user:          ${ADMIN_USER}
Admin password:      ${ADMIN_PASSWORD}
Admin email:         ${ADMIN_EMAIL}

API credentials — paste into backend/.env:
----------------------------------------------------------------------
AKENEO_BASE_URL=${SITE_URL}
AKENEO_CLIENT_ID=${client_id:-<see-above>}
AKENEO_SECRET=${client_secret:-<see-above>}
AKENEO_USERNAME=${ADMIN_USER}
AKENEO_PASSWORD=${ADMIN_PASSWORD}
----------------------------------------------------------------------

If client_id / secret didn't print above, run manually:
  $COMPOSE exec akeneo bin/console pim:oauth-server:create-client retail-os \\
    --grant_type=password --grant_type=refresh_token

Sanity check (token round-trip):
  curl -s -u "<client_id>:<secret>" \\
    -X POST '${SITE_URL}/api/oauth/v1/token' \\
    -d 'grant_type=password&username=${ADMIN_USER}&password=${ADMIN_PASSWORD}'

Reset path: \`make akeneo-nuke && make akeneo-up && make akeneo-bootstrap\`.
NOTE: First boot is slow — composer install + Liquibase-style migrations
take ~10 minutes on a fresh machine.
======================================================================
BANNER
