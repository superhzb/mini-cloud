# {{name}} — the standard mini-cloud task entrypoints (same names in every mini-cloud repo).

.PHONY: help setup run worker test check lint fmt migrate seed check-live

UV := uv

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

setup: ## One documented bootstrap: install pinned deps + seed .env (scorecard #1)
	$(UV) sync
	@test -f .env || cp .env.example .env
	@echo "setup complete — edit .env, then: make run"

run: ## Run the web server on the canonical PORT
	$(UV) run {{name}}

worker: ## Run the background job worker (separate process)
	$(UV) run {{name}}-worker

migrate: ## Apply DB migrations (also runs at app startup)
	$(UV) run python -c "from {{package}}.resources import build_resources; build_resources(); print('migrated')"

seed: ## Enqueue one demo note through the running API
	@curl -fsS -X POST localhost:$${PORT:-{{api_port}}}/notes -H 'content-type: application/json' \
	  -d '{"text":"hello from {{name}}"}' && echo

test: ## Run the unit test suite (no services required)
	$(UV) run pytest

lint: ## Lint + format-check (shared ruff config) — scorecard #4
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Auto-fix lint + format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

check: ## Full gate: lint + typecheck + tests, non-zero on failure (scorecard #3)
	$(UV) run ruff check .
	$(UV) run pyright src
	$(UV) run pytest -q

check-live: ## Full gate against an EPHEMERAL throwaway Postgres (needs docker)
	./scripts/check-live.sh
