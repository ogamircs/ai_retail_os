# AI Retail OS — convenience targets

ERPNEXT_DIR := infra/erpnext
ERPNEXT_COMPOSE := docker compose -p ai-retail-erpnext -f $(ERPNEXT_DIR)/docker-compose.yml

MAUTIC_DIR := infra/mautic
MAUTIC_COMPOSE := docker compose -p ai-retail-mautic -f $(MAUTIC_DIR)/docker-compose.yml

.PHONY: help \
        erpnext-up erpnext-down erpnext-bootstrap erpnext-seed erpnext-logs erpnext-status erpnext-nuke \
        mautic-up mautic-down mautic-bootstrap mautic-seed mautic-logs mautic-status mautic-nuke

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
