#!/usr/bin/env bash
# One-shot bootstrap for the demo ERPNext site.
# Idempotent: safe to re-run — existing sites/keys are detected and skipped.

set -euo pipefail

SITE_NAME="${ERPNEXT_SITE:-retail.localhost}"
ADMIN_PASSWORD="${ERPNEXT_ADMIN_PASSWORD:-retail-admin}"
DB_PASSWORD="${DB_PASSWORD:-retail-db}"

cd "$(dirname "$0")"

COMPOSE="docker compose -p ai-retail-erpnext -f docker-compose.yml"

echo ">> ensuring stack is up"
$COMPOSE up -d

echo ">> waiting for backend to be ready"
for _ in $(seq 1 60); do
  if $COMPOSE exec -T backend bench --version >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if $COMPOSE exec -T backend bash -c "[ -d sites/${SITE_NAME} ]"; then
  echo ">> site '${SITE_NAME}' already exists — skipping new-site"
else
  echo ">> creating site '${SITE_NAME}' (this takes a couple of minutes on first run)"
  $COMPOSE exec -T backend bench new-site \
    --mariadb-user-host-login-scope=% \
    --admin-password "${ADMIN_PASSWORD}" \
    --db-root-password "${DB_PASSWORD}" \
    --install-app erpnext \
    --set-default \
    "${SITE_NAME}"
fi

echo ">> setting Administrator password (idempotent)"
$COMPOSE exec -T backend bench --site "${SITE_NAME}" set-admin-password "${ADMIN_PASSWORD}" \
  >/dev/null 2>&1 || true

echo ">> generating API key + secret for Administrator"
# `generate_keys` is Frappe's official server-side method. It rotates the
# secret on every call, so we only invoke it if Administrator has no key yet.
HAS_KEY=$(
  $COMPOSE exec -T backend bench --site "${SITE_NAME}" execute frappe.client.get_value \
    --kwargs "{'doctype':'User','filters':{'name':'Administrator'},'fieldname':'api_key'}" 2>/dev/null \
  || echo "{}"
)

if echo "$HAS_KEY" | grep -q '"api_key": *"[a-z0-9]\+"'; then
  echo "   Administrator already has an api_key. To rotate, re-run with FORCE_REKEY=1."
  if [ "${FORCE_REKEY:-0}" = "1" ]; then
    KEYS=$($COMPOSE exec -T backend bench --site "${SITE_NAME}" execute \
      frappe.core.doctype.user.user.generate_keys --kwargs "{'user':'Administrator'}" 2>/dev/null | tail -1)
  else
    KEYS=$HAS_KEY
  fi
else
  KEYS=$($COMPOSE exec -T backend bench --site "${SITE_NAME}" execute \
    frappe.core.doctype.user.user.generate_keys --kwargs "{'user':'Administrator'}" 2>/dev/null | tail -1)
fi

cat <<BANNER

======================================================================
ERPNext is up at:  http://localhost:8080
Site:              ${SITE_NAME}
Admin user:        Administrator
Admin password:    ${ADMIN_PASSWORD}

API credentials — paste these into backend/.env:
----------------------------------------------------------------------
${KEYS}
----------------------------------------------------------------------

Sanity check:
  curl -s http://localhost:8080/api/method/frappe.auth.get_logged_user \\
    -H 'Authorization: token <api_key>:<api_secret>'
  # → {"message":"Administrator"}

NOTE: Frappe encrypts api_secret at rest. The secret is only readable in
plaintext at the moment generate_keys runs. If you lose it, re-run this
script with FORCE_REKEY=1 to rotate.
======================================================================
BANNER
