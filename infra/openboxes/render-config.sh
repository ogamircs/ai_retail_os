#!/usr/bin/env bash
# Render infra/openboxes/openboxes-config.properties from the template
# using OPENBOXES_DB_* env values (default to the compose defaults).
#
# Invoked by both `make openboxes-up` and `make openboxes-bootstrap` so
# the file the compose mount needs always exists before Tomcat boots —
# even if the operator did `make openboxes-up` standalone.
#
# Grails' native .properties loader doesn't expand env vars; render-time
# substitution is the only honest way to plumb the documented
# OPENBOXES_DB_* overrides through to dataSource.password.

set -euo pipefail

DB_USER="${OPENBOXES_DB_USER:-openboxes}"
DB_PASSWORD="${OPENBOXES_DB_PASSWORD:-openboxes}"
DB_NAME="${OPENBOXES_DB_NAME:-openboxes}"

cd "$(dirname "$0")"

sed \
  -e "s|@@DB_NAME@@|${DB_NAME}|g" \
  -e "s|@@DB_USER@@|${DB_USER}|g" \
  -e "s|@@DB_PASSWORD@@|${DB_PASSWORD}|g" \
  openboxes-config.properties.template \
  > openboxes-config.properties
