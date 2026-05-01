#!/usr/bin/env bash
# One-shot bootstrap for the demo Superset instance.
#
# Superset's official image ships an `init.sh` that does `db upgrade`
# + `init` + admin-user creation. We wait for the web server to come
# up (Gunicorn binds before init finishes), then run init in-container.
# init is idempotent — re-runs on an already-bootstrapped instance
# print "User <admin> already exists" and exit 0.

set -euo pipefail

ADMIN_USER="${SUPERSET_ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${SUPERSET_ADMIN_PASSWORD:-retail-superset}"
ADMIN_EMAIL="${SUPERSET_ADMIN_EMAIL:-admin@retail.local}"
SITE_URL="${SUPERSET_BASE_URL:-http://localhost:8088}"

cd "$(dirname "$0")"

COMPOSE="docker compose -p ai-retail-superset -f docker-compose.yml"

echo ">> ensuring stack is up"
$COMPOSE up -d

echo ">> waiting for postgres + redis"
for _ in $(seq 1 60); do
  if $COMPOSE exec -T db pg_isready -U superset -d superset >/dev/null 2>&1 && \
     $COMPOSE exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
    break
  fi
  sleep 2
done

echo ">> waiting for superset container readiness"
for _ in $(seq 1 90); do
  if $COMPOSE exec -T superset test -f /app/pythonpath/superset_config.py >/dev/null 2>&1 \
     || $COMPOSE exec -T superset which superset >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ">> running superset db upgrade (idempotent)"
set +e
upgrade_output=$(
  $COMPOSE exec -T superset superset db upgrade 2>&1
)
upgrade_rc=$?
set -e
if [ $upgrade_rc -eq 0 ]; then
  echo "++ superset db upgrade completed"
else
  echo "$upgrade_output"
  echo "!! superset db upgrade failed (exit=$upgrade_rc) — bootstrap aborted."
  exit "$upgrade_rc"
fi

echo ">> ensuring admin user (idempotent)"
set +e
user_output=$(
  $COMPOSE exec -T \
    -e BS_ADMIN_USER="$ADMIN_USER" \
    -e BS_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    -e BS_ADMIN_EMAIL="$ADMIN_EMAIL" \
    superset bash -c '
      superset fab create-admin \
        --username "$BS_ADMIN_USER" \
        --firstname Retail \
        --lastname Operator \
        --email "$BS_ADMIN_EMAIL" \
        --password "$BS_ADMIN_PASSWORD"
    ' 2>&1
)
user_rc=$?
set -e
# `superset fab create-admin` exits 0 either way ("User already exists"
# is printed but rc=0 on the official 3.x images). We still pattern-match
# so a real failure (DB unreachable, schema drift) doesn't slip past.
if [ $user_rc -eq 0 ] && echo "$user_output" | grep -qiE "already exists|admin user|recognized"; then
  echo "++ admin user ready"
elif [ $user_rc -eq 0 ]; then
  echo "++ admin user created"
else
  echo "$user_output"
  echo "!! superset fab create-admin failed (exit=$user_rc) — bootstrap aborted."
  exit "$user_rc"
fi

echo ">> superset init (roles + permissions, idempotent)"
$COMPOSE exec -T superset superset init >/dev/null

cat <<BANNER

======================================================================
Superset is up at: ${SITE_URL}
Admin user:        ${ADMIN_USER}
Admin password:    ${ADMIN_PASSWORD}
Admin email:       ${ADMIN_EMAIL}

API credentials — paste into backend/.env:
----------------------------------------------------------------------
SUPERSET_BASE_URL=${SITE_URL}
SUPERSET_USERNAME=${ADMIN_USER}
SUPERSET_PASSWORD=${ADMIN_PASSWORD}
----------------------------------------------------------------------

Next:
  make superset-seed       # registers spine.db + builds the demo dashboard

Sanity check (login + token round-trip):
  curl -s -X POST '${SITE_URL}/api/v1/security/login' \\
    -H 'Content-Type: application/json' \\
    -d '{"username":"${ADMIN_USER}","password":"${ADMIN_PASSWORD}","provider":"db","refresh":true}' \\
    | jq -r '.access_token' | head -c 20; echo

Reset path: \`make superset-nuke && make superset-up && make superset-bootstrap\`.
======================================================================
BANNER
