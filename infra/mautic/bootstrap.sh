#!/usr/bin/env bash
# One-shot bootstrap for the demo Mautic instance.
# Idempotent: safe to re-run — existing install is detected and skipped.
#
# What it does:
#   1. Brings the stack up (docker compose up -d).
#   2. Waits for the web container to respond.
#   3. Runs `php bin/console mautic:install` if Mautic isn't installed yet.
#   4. Enables basic-auth on the API so /api/* accepts the admin creds.
#   5. Prints the admin URL + credentials + a sanity curl.

set -euo pipefail

ADMIN_EMAIL="${MAUTIC_ADMIN_EMAIL:-admin@retail.local}"
ADMIN_USER="${MAUTIC_ADMIN_USER:-admin}"
ADMIN_PASSWORD="${MAUTIC_ADMIN_PASSWORD:-retail-mautic}"
ADMIN_FIRST="${MAUTIC_ADMIN_FIRST:-Retail}"
ADMIN_LAST="${MAUTIC_ADMIN_LAST:-Operator}"
DB_USER="${MAUTIC_DB_USER:-mautic}"
DB_PASSWORD="${MAUTIC_DB_PASSWORD:-retail-db}"
DB_NAME="${MAUTIC_DB_NAME:-mautic}"
SITE_URL="${MAUTIC_SITE_URL:-http://localhost:8081}"

cd "$(dirname "$0")"

COMPOSE="docker compose -p ai-retail-mautic -f docker-compose.yml"

echo ">> ensuring stack is up"
$COMPOSE up -d

echo ">> waiting for mautic_web to be ready"
for _ in $(seq 1 90); do
  if $COMPOSE exec -T mautic_web bash -c "test -f /var/www/html/bin/console" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ">> waiting for the database"
for _ in $(seq 1 60); do
  if $COMPOSE exec -T db mysqladmin ping -h localhost --password="${DB_PASSWORD}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# Mautic writes config/local.php once installed. Use it as the install marker.
if $COMPOSE exec -T mautic_web bash -c "test -s /var/www/html/config/local.php"; then
  echo ">> mautic already installed — skipping mautic:install"
else
  echo ">> running mautic:install (first run takes 30-90s)"
  $COMPOSE exec -T mautic_web bash -c "
    php bin/console mautic:install \
      --admin_email='${ADMIN_EMAIL}' \
      --admin_username='${ADMIN_USER}' \
      --admin_password='${ADMIN_PASSWORD}' \
      --admin_firstname='${ADMIN_FIRST}' \
      --admin_lastname='${ADMIN_LAST}' \
      --db_driver=pdo_mysql \
      --db_host=db \
      --db_port=3306 \
      --db_user='${DB_USER}' \
      --db_password='${DB_PASSWORD}' \
      --db_name='${DB_NAME}' \
      --mailer_from_email='${ADMIN_EMAIL}' \
      --mailer_from_name='AI Retail OS' \
      --mailer_dsn='null://null' \
      --no-interaction \
      '${SITE_URL}'
  "
fi

echo ">> enabling API + basic-auth (idempotent)"
# Mautic stores config in app/bundles/CoreBundle/Config/Config.php-keyed values
# inside config/local.php. We append the API toggles via the standard
# `config:set` console command if available, else patch local.php directly.
$COMPOSE exec -T mautic_web bash -c "
  php bin/console mautic:config:set api_enabled true >/dev/null 2>&1 || true
  php bin/console mautic:config:set api_enable_basic_auth true >/dev/null 2>&1 || true
  # Fallback: ensure the keys exist in local.php even if the console method
  # isn't available on this Mautic version (5.x has shifted these around).
  php -r \"
    \\\$cfg = require '/var/www/html/config/local.php';
    \\\$cfg['api_enabled'] = true;
    \\\$cfg['api_enable_basic_auth'] = true;
    file_put_contents('/var/www/html/config/local.php', '<?php' . PHP_EOL . 'return ' . var_export(\\\$cfg, true) . ';' . PHP_EOL);
  \"
  php bin/console cache:clear --env=prod >/dev/null 2>&1 || true
"

cat <<BANNER

======================================================================
Mautic is up at:   ${SITE_URL}
Admin user:        ${ADMIN_USER}
Admin password:    ${ADMIN_PASSWORD}
Admin email:       ${ADMIN_EMAIL}

API credentials — paste into backend/.env:
----------------------------------------------------------------------
MAUTIC_BASE_URL=${SITE_URL}
MAUTIC_USERNAME=${ADMIN_USER}
MAUTIC_PASSWORD=${ADMIN_PASSWORD}
----------------------------------------------------------------------

Sanity check (basic auth on /api/contacts):
  curl -s -u '${ADMIN_USER}:${ADMIN_PASSWORD}' \\
    '${SITE_URL}/api/contacts?limit=1' | head -c 400; echo

Reset path: \`make mautic-nuke && make mautic-up && make mautic-bootstrap\`.
======================================================================
BANNER
