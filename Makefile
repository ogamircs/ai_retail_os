# AI Retail OS — convenience targets

ERPNEXT_DIR := infra/erpnext
ERPNEXT_COMPOSE := docker compose -p ai-retail-erpnext -f $(ERPNEXT_DIR)/docker-compose.yml

MAUTIC_DIR := infra/mautic
MAUTIC_COMPOSE := docker compose -p ai-retail-mautic -f $(MAUTIC_DIR)/docker-compose.yml

MEDUSA_DIR := infra/medusa
MEDUSA_COMPOSE := docker compose -p ai-retail-medusa -f $(MEDUSA_DIR)/docker-compose.yml

OPENBOXES_DIR := infra/openboxes
OPENBOXES_COMPOSE := docker compose -p ai-retail-openboxes -f $(OPENBOXES_DIR)/docker-compose.yml

.PHONY: help \
        erpnext-up erpnext-down erpnext-bootstrap erpnext-seed erpnext-logs erpnext-status erpnext-nuke \
        mautic-up mautic-down mautic-bootstrap mautic-seed mautic-logs mautic-status mautic-nuke \
        medusa-up medusa-down medusa-bootstrap medusa-seed medusa-logs medusa-status medusa-nuke \
        openboxes-up openboxes-down openboxes-bootstrap openboxes-seed openboxes-logs openboxes-status openboxes-nuke openboxes-config

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
