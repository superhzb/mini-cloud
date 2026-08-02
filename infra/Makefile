# mini-cloud infra stack. Requires Docker (Docker Desktop or colima) + docker compose.
COMPOSE := docker compose

# Host address the published ports bind to. Default 0.0.0.0 so the stack is
# reachable from other machines on the LAN (e.g. a second Mac). Override to keep
# it loopback-only: `INFRA_BIND_ADDR=127.0.0.1 make up`.
INFRA_BIND_ADDR ?= 0.0.0.0
export INFRA_BIND_ADDR

.PHONY: help up down restart ps logs config pull backup grafana minio adminer psql project analytics-init identity-init

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

up: ## Start the whole stack (detached)
	@test -f .env || { echo "no .env — run: cp .env.example .env"; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "docker daemon down — starting colima…"; colima start; }
	$(COMPOSE) up -d
	@echo "Bound on $(INFRA_BIND_ADDR) (LAN-reachable unless overridden to 127.0.0.1)"
	@echo "Grafana → http://localhost:13000   MinIO console → http://localhost:19001   Adminer (DB) → http://localhost:18432"

down: ## Stop the stack (keeps volumes/data)
	$(COMPOSE) down

restart: ## Restart the stack
	$(COMPOSE) restart

ps: ## Show service health
	$(COMPOSE) ps

logs: ## Tail all logs
	$(COMPOSE) logs -f --tail=100

config: ## Validate & render the effective compose config
	$(COMPOSE) config

pull: ## Pull pinned images
	$(COMPOSE) pull

backup: ## Dump Postgres + mirror MinIO into ./backups/<timestamp>
	./scripts/backup.sh

project: ## Create a DB+bucket for a project: make project NAME=demo-x
	@test -n "$(NAME)" || { echo "usage: make project NAME=<name>"; exit 1; }
	./scripts/create-project.sh $(NAME)
	@if test -d "../examples/$(NAME)/grafana"; then \
	  for f in ../examples/$(NAME)/grafana/*.json; do \
	    test -f "$$f" || continue; \
	    base=$$(basename "$$f" .json); \
	    if [ "$$base" = "dashboard" ]; then dest="app-$(NAME).json"; \
	    else dest="app-$(NAME)-$$base.json"; fi; \
	    cp "$$f" "config/grafana/dashboards/$$dest"; \
	    echo "==> Grafana dashboard provisioned: $$dest"; \
	  done; \
	fi

analytics-init: ## Create the shared `analytics` DB + read-only Grafana role + schema
	./scripts/analytics-init.sh

identity-init: ## Create the empty `identity` DB + owner role (schema owned by the identity service)
	./scripts/identity-init.sh

grafana: ## Open Grafana
	open http://localhost:13000

minio: ## Open MinIO console
	open http://localhost:19001

adminer: ## Open Adminer DB browser (server: postgres, login from .env, then pick a DB)
	open http://localhost:18432

psql: ## psql shell into Postgres
	$(COMPOSE) exec postgres sh -lc 'psql -U "$$POSTGRES_USER"'
