# AI Retail OS — convenience targets

ERPNEXT_DIR := infra/erpnext
ERPNEXT_COMPOSE := docker compose -p ai-retail-erpnext -f $(ERPNEXT_DIR)/docker-compose.yml

.PHONY: help erpnext-up erpnext-down erpnext-bootstrap erpnext-seed erpnext-logs erpnext-status erpnext-nuke

help:
	@echo "Targets:"
	@echo "  erpnext-up         start ERPNext stack (mariadb + redis + frappe + nginx on :8080)"
	@echo "  erpnext-bootstrap  create the retail.localhost site, install ERPNext app, generate API keys"
	@echo "  erpnext-seed       project the spine demo data into ERPNext (idempotent)"
	@echo "  erpnext-status     show running containers"
	@echo "  erpnext-logs       tail logs from the stack"
	@echo "  erpnext-down       stop the stack (volumes preserved)"
	@echo "  erpnext-nuke       stop and wipe volumes (full reset)"

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
