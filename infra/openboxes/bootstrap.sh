#!/usr/bin/env bash
# One-shot bootstrap for the demo OpenBoxes instance.
#
# OpenBoxes' Grails app runs Liquibase migrations + seeds a default admin
# user (`openboxes` / `password`) on first Tomcat boot — there's no
# external migrate command we have to call. This script just brings the
# stack up, waits for Tomcat to respond, and prints the admin URL +
# credentials + the env block to paste into backend/.env once the
# operator has minted an API token via the OpenBoxes UI.

set -euo pipefail

ADMIN_USER="${OPENBOXES_ADMIN_USER:-openboxes}"
ADMIN_PASSWORD="${OPENBOXES_ADMIN_PASSWORD:-password}"
DB_USER="${OPENBOXES_DB_USER:-openboxes}"
DB_PASSWORD="${OPENBOXES_DB_PASSWORD:-openboxes}"
DB_NAME="${OPENBOXES_DB_NAME:-openboxes}"
SITE_URL="${OPENBOXES_SITE_URL:-http://localhost:8082}"

cd "$(dirname "$0")"

COMPOSE="docker compose -p ai-retail-openboxes -f docker-compose.yml"

echo ">> rendering openboxes-config.properties from template"
bash render-config.sh

echo ">> ensuring stack is up (builds the openboxes image on first run)"
$COMPOSE up -d

echo ">> waiting for mysql"
for _ in $(seq 1 90); do
  if $COMPOSE exec -T -e MYSQL_PWD="${DB_PASSWORD}" db \
      mysqladmin ping -h localhost -u openboxes >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ">> waiting for OpenBoxes Tomcat to come up (Liquibase migrations on first boot can take 3-5 min)"
ok=""
for _ in $(seq 1 180); do
  # OpenBoxes serves a login page at / once Liquibase finishes; we
  # accept anything 2xx/3xx (Tomcat redirects unauthenticated GETs
  # to /openboxes/auth/login).
  code=$(curl -s -o /dev/null -w "%{http_code}" "${SITE_URL}/" || echo "000")
  if [[ "$code" =~ ^[23] ]]; then
    ok="yes"
    break
  fi
  sleep 2
done

if [ -z "$ok" ]; then
  echo "!! OpenBoxes didn't come up in time. Check \`make openboxes-logs\`."
  echo "   (First boot is slow because Liquibase has to apply 200+ changesets)"
  exit 1
fi

cat <<BANNER

======================================================================
OpenBoxes is up at: ${SITE_URL}
Admin user:         ${ADMIN_USER}
Admin password:     ${ADMIN_PASSWORD}   (Liquibase seeds this default;
                                          you'll be prompted to change
                                          it on first login)

API credentials — once you've signed in once and changed the password,
mint an API token via the user-settings page in the OpenBoxes UI, then
paste this block into backend/.env:
----------------------------------------------------------------------
OPENBOXES_BASE_URL=${SITE_URL}
OPENBOXES_USERNAME=${ADMIN_USER}
OPENBOXES_PASSWORD=<your-new-password>
# OPTIONAL: prefer this over USERNAME/PASSWORD once you've minted a token
# OPENBOXES_API_TOKEN=...
----------------------------------------------------------------------

Sanity check (login flow):
  curl -s -X POST -H 'Content-Type: application/json' \\
    -d '{"username":"${ADMIN_USER}","password":"<your-new-password>"}' \\
    '${SITE_URL}/api/login' | head -c 200; echo

Reset path: \`make openboxes-nuke && make openboxes-up && make openboxes-bootstrap\`.
NOTE: First boot is slow — the WAR runs Liquibase migrations across
200+ changesets the first time mysql comes up clean. Subsequent boots
take seconds.
======================================================================
BANNER
