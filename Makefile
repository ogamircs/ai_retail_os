# AI Retail OS — convenience targets

ERPNEXT_DIR := infra/erpnext
ERPNEXT_COMPOSE := docker compose -p ai-retail-erpnext -f $(ERPNEXT_DIR)/docker-compose.yml

MAUTIC_DIR := infra/mautic
MAUTIC_COMPOSE := docker compose -p ai-retail-mautic -f $(MAUTIC_DIR)/docker-compose.yml

MEDUSA_DIR := infra/medusa
MEDUSA_COMPOSE := docker compose -p ai-retail-medusa -f $(MEDUSA_DIR)/docker-compose.yml

OPENBOXES_DIR := infra/openboxes
OPENBOXES_COMPOSE := docker compose -p ai-retail-openboxes -f $(OPENBOXES_DIR)/docker-compose.yml

AKENEO_DIR := infra/akeneo
AKENEO_COMPOSE := docker compose -p ai-retail-akeneo -f $(AKENEO_DIR)/docker-compose.yml

SUPERSET_DIR := infra/superset
SUPERSET_COMPOSE := docker compose -p ai-retail-superset -f $(SUPERSET_DIR)/docker-compose.yml

MLFLOW_DIR := infra/mlflow
MLFLOW_COMPOSE := docker compose -p ai-retail-mlflow -f $(MLFLOW_DIR)/docker-compose.yml

.PHONY: help \
        erpnext-up erpnext-down erpnext-bootstrap erpnext-seed erpnext-logs erpnext-status erpnext-nuke \
        mautic-up mautic-down mautic-bootstrap mautic-seed mautic-logs mautic-status mautic-nuke \
        medusa-up medusa-down medusa-bootstrap medusa-seed medusa-logs medusa-status medusa-nuke \
        openboxes-up openboxes-down openboxes-bootstrap openboxes-seed openboxes-logs openboxes-status openboxes-nuke openboxes-config \
        akeneo-up akeneo-down akeneo-bootstrap akeneo-seed akeneo-logs akeneo-status akeneo-nuke \
        superset-up superset-down superset-bootstrap superset-seed superset-logs superset-status superset-nuke \
        mlflow-up mlflow-down mlflow-logs mlflow-status mlflow-nuke \
        shopify-seed \
        test test-live test-live-erpnext test-live-mautic test-live-medusa test-live-openboxes test-live-akeneo test-live-superset test-live-shopify

help:
	@echo "ERPNext:"
	@echo "  erpnext-up         start ERPNext stack (mariadb + redis + frappe + nginx on :8080)"
	@echo "  erpnext-bootstrap  create the retail.localhost site, install ERPNext app, generate API keys"
	@echo "  erpnext-seed       project the spine demo data into ERPNext (idempotent)"
	@echo "  erpnext-status     show running containers"
	@echo "  erpnext-logs       tail logs from the stack"
	@echo "  erpnext-down       stop the stack (volumes preserved)"
	@echo "  erpnext-nuke       stop and wipe volumes (full reset)"
	@echo ""
	@echo "Mautic:"
	@echo "  mautic-up          start Mautic stack (mariadb + apache + cron + worker on :8081)"
	@echo "  mautic-bootstrap   run mautic:install, enable API basic-auth, print admin creds"
	@echo "  mautic-seed        project the spine demo data into Mautic (idempotent)"
	@echo "  mautic-status      show running containers"
	@echo "  mautic-logs        tail logs from the stack"
	@echo "  mautic-down        stop the stack (volumes preserved)"
	@echo "  mautic-nuke        stop and wipe volumes (full reset)"
	@echo ""
	@echo "Medusa:"
	@echo "  medusa-up          build + start Medusa stack (postgres + redis + medusa on :9000)"
	@echo "  medusa-bootstrap   db:migrate + create admin user (idempotent)"
	@echo "  medusa-seed        project the spine demo data into Medusa (idempotent)"
	@echo "  medusa-status      show running containers"
	@echo "  medusa-logs        tail logs from the stack"
	@echo "  medusa-down        stop the stack (volumes preserved)"
	@echo "  medusa-nuke        stop and wipe volumes (full reset)"
	@echo ""
	@echo "OpenBoxes:"
	@echo "  openboxes-up          build + start OpenBoxes stack (mysql + tomcat on :8082)"
	@echo "  openboxes-bootstrap   wait for Liquibase migrations + print admin creds"
	@echo "  openboxes-seed        project the spine demo data into OpenBoxes (idempotent)"
	@echo "  openboxes-status      show running containers"
	@echo "  openboxes-logs        tail logs from the stack"
	@echo "  openboxes-down        stop the stack (volumes preserved)"
	@echo "  openboxes-nuke        stop and wipe volumes (full reset)"
	@echo ""
	@echo "Akeneo PIM:"
	@echo "  akeneo-up             build + start Akeneo stack (mysql + opensearch + akeneo on :8083)"
	@echo "  akeneo-bootstrap      pim:installer:db + admin user + OAuth client (idempotent)"
	@echo "  akeneo-seed           project the spine demo data into Akeneo (idempotent)"
	@echo "  akeneo-status         show running containers"
	@echo "  akeneo-logs           tail logs from the stack"
	@echo "  akeneo-down           stop the stack (volumes preserved)"
	@echo "  akeneo-nuke           stop and wipe volumes (full reset)"
	@echo ""
	@echo "Superset:"
	@echo "  superset-up           start Superset stack (postgres + redis + superset on :8088)"
	@echo "  superset-bootstrap    db upgrade + admin user + roles (idempotent)"
	@echo "  superset-seed         register spine.db + create datasets + build demo dashboard"
	@echo "  superset-status       show running containers"
	@echo "  superset-logs         tail logs from the stack"
	@echo "  superset-down         stop the stack (volumes preserved)"
	@echo "  superset-nuke         stop and wipe volumes (full reset)"
	@echo ""
	@echo "MLflow:"
	@echo "  mlflow-up             start MLflow stack (postgres + minio + mlflow on :5500)"
	@echo "  mlflow-status         show running containers"
	@echo "  mlflow-logs           tail logs from the stack"
	@echo "  mlflow-down           stop the stack (volumes preserved)"
	@echo "  mlflow-nuke           stop and wipe volumes (full reset)"
	@echo ""
	@echo "Shopify Plus (cloud-hosted, no local stack):"
	@echo "  shopify-seed          project the spine demo data into a Shopify dev store (idempotent)"
	@echo ""
	@echo "Tests (mock-only by default; live suites are opt-in):"
	@echo "  test                  run the full backend test suite (mock-only — live suites skipped)"
	@echo "  test-live             run mock + every live suite (RUN_LIVE_TESTS=1)"
	@echo "  test-live-erpnext     run only the ERPNext live suite (RUN_ERPNEXT_LIVE=1)"
	@echo "  test-live-mautic      run only the Mautic live suite (RUN_MAUTIC_LIVE=1)"
	@echo "  test-live-medusa      run only the Medusa live suite (RUN_MEDUSA_LIVE=1)"
	@echo "  test-live-openboxes   run only the OpenBoxes live suite (RUN_OPENBOXES_LIVE=1)"
	@echo "  test-live-akeneo      run only the Akeneo live suite (RUN_AKENEO_LIVE=1)"
	@echo "  test-live-superset    run only the Superset live suite (RUN_SUPERSET_LIVE=1)"
	@echo "  test-live-shopify     run only the Shopify live suite (RUN_SHOPIFY_LIVE=1)"

erpnext-up:
	$(ERPNEXT_COMPOSE) up -d

erpnext-down:
	$(ERPNEXT_COMPOSE) down

erpnext-nuke:
	$(ERPNEXT_COMPOSE) down -v

erpnext-status:
	$(ERPNEXT_COMPOSE) ps

erpnext-logs:
	$(ERPNEXT_COMPOSE) logs -f --tail=50

erpnext-bootstrap:
	bash $(ERPNEXT_DIR)/bootstrap.sh

erpnext-seed:
	python3 $(ERPNEXT_DIR)/seed.py

mautic-up:
	$(MAUTIC_COMPOSE) up -d

mautic-down:
	$(MAUTIC_COMPOSE) down

mautic-nuke:
	$(MAUTIC_COMPOSE) down -v

mautic-status:
	$(MAUTIC_COMPOSE) ps

mautic-logs:
	$(MAUTIC_COMPOSE) logs -f --tail=50

mautic-bootstrap:
	bash $(MAUTIC_DIR)/bootstrap.sh

mautic-seed:
	python3 $(MAUTIC_DIR)/seed.py

medusa-up:
	$(MEDUSA_COMPOSE) up -d --build

medusa-down:
	$(MEDUSA_COMPOSE) down

medusa-nuke:
	$(MEDUSA_COMPOSE) down -v

medusa-status:
	$(MEDUSA_COMPOSE) ps

medusa-logs:
	$(MEDUSA_COMPOSE) logs -f --tail=50

medusa-bootstrap:
	bash $(MEDUSA_DIR)/bootstrap.sh

medusa-seed:
	python3 $(MEDUSA_DIR)/seed.py

openboxes-config:
	bash $(OPENBOXES_DIR)/render-config.sh

openboxes-up: openboxes-config
	$(OPENBOXES_COMPOSE) up -d --build

openboxes-down:
	$(OPENBOXES_COMPOSE) down

openboxes-nuke:
	$(OPENBOXES_COMPOSE) down -v

openboxes-status:
	$(OPENBOXES_COMPOSE) ps

openboxes-logs:
	$(OPENBOXES_COMPOSE) logs -f --tail=50

openboxes-bootstrap:
	bash $(OPENBOXES_DIR)/bootstrap.sh

openboxes-seed:
	python3 $(OPENBOXES_DIR)/seed.py

akeneo-up:
	$(AKENEO_COMPOSE) up -d --build

akeneo-down:
	$(AKENEO_COMPOSE) down

akeneo-nuke:
	$(AKENEO_COMPOSE) down -v

akeneo-status:
	$(AKENEO_COMPOSE) ps

akeneo-logs:
	$(AKENEO_COMPOSE) logs -f --tail=50

akeneo-bootstrap:
	bash $(AKENEO_DIR)/bootstrap.sh

akeneo-seed:
	python3 $(AKENEO_DIR)/seed.py

superset-up:
	$(SUPERSET_COMPOSE) up -d

superset-down:
	$(SUPERSET_COMPOSE) down

superset-nuke:
	$(SUPERSET_COMPOSE) down -v

superset-status:
	$(SUPERSET_COMPOSE) ps

superset-logs:
	$(SUPERSET_COMPOSE) logs -f --tail=50

superset-bootstrap:
	bash $(SUPERSET_DIR)/bootstrap.sh

superset-seed:
	python3 $(SUPERSET_DIR)/seed.py

mlflow-up:
	$(MLFLOW_COMPOSE) up -d

mlflow-down:
	$(MLFLOW_COMPOSE) down

mlflow-nuke:
	$(MLFLOW_COMPOSE) down -v

mlflow-status:
	$(MLFLOW_COMPOSE) ps

mlflow-logs:
	$(MLFLOW_COMPOSE) logs -f --tail=50

shopify-seed:
	python3 infra/shopify/seed.py

# ---- Test runners ---------------------------------------------------------
# `test` is the default — every live suite skips because no opt-in flag is set.
# `test-live` and `test-live-<system>` flip exactly one flag at a time so a
# brittle live test (e.g. ERPNext drift) can never break the default suite.

PY := cd backend && ./.venv/bin/python -m unittest discover -s tests

test:
	$(PY)

test-live:
	cd backend && RUN_LIVE_TESTS=1 ./.venv/bin/python -m unittest discover -s tests

test-live-erpnext:
	cd backend && RUN_ERPNEXT_LIVE=1 ./.venv/bin/python -m unittest tests.test_integrations_erpnext_live

test-live-mautic:
	cd backend && RUN_MAUTIC_LIVE=1 ./.venv/bin/python -m unittest tests.test_integrations_mautic_live

test-live-medusa:
	cd backend && RUN_MEDUSA_LIVE=1 ./.venv/bin/python -m unittest tests.test_integrations_medusa_live

test-live-openboxes:
	cd backend && RUN_OPENBOXES_LIVE=1 ./.venv/bin/python -m unittest tests.test_integrations_openboxes_live

test-live-akeneo:
	cd backend && RUN_AKENEO_LIVE=1 ./.venv/bin/python -m unittest tests.test_integrations_akeneo_live

test-live-superset:
	cd backend && RUN_SUPERSET_LIVE=1 ./.venv/bin/python -m unittest tests.test_integrations_superset_live

test-live-shopify:
	cd backend && RUN_SHOPIFY_LIVE=1 ./.venv/bin/python -m unittest tests.test_integrations_shopify_live

# Track 6 — GBrain. Not a docker-compose stack — GBrain is a Bun-native
# CLI installed via `git clone + bun install + bun link`. These targets
# wrap the lifecycle commands so cockpit operators stay inside `make ...`
# semantics. `gbrain-up` runs in foreground (long-lived HTTP server).
GBRAIN_PORT := 8787

gbrain-up:
	@command -v gbrain >/dev/null 2>&1 || { echo "gbrain not on PATH — see infra/gbrain/README.md"; exit 1; }
	gbrain serve --http --port $(GBRAIN_PORT)

gbrain-down:
	@pkill -f "gbrain serve --http --port $(GBRAIN_PORT)" || echo "no gbrain process on :$(GBRAIN_PORT)"

gbrain-doctor:
	@command -v gbrain >/dev/null 2>&1 || { echo "gbrain not on PATH — see infra/gbrain/README.md"; exit 1; }
	gbrain doctor

gbrain-status:
	@curl -fsS http://localhost:$(GBRAIN_PORT)/health 2>/dev/null && echo "" || echo "gbrain not reachable on :$(GBRAIN_PORT)"
