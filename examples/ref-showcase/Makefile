# ref-showcase — the standard mini-cloud task entrypoints (same names as every mini-cloud repo,
# scorecard metric #2). `make setup` then `make check` should work on a fresh clone with no
# services running.

.PHONY: help setup run ui worker test check lint fmt migrate seed seed-live check-live

UV := uv

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

setup: ## One documented bootstrap: install pinned deps + seed .env (scorecard #1)
	$(UV) sync
	@test -f .env || cp .env.example .env
	@echo "setup complete — edit .env, then: make run"

run: ## Run the web server on the canonical PORT
	$(UV) run ref-showcase

ui: run ## Run the same server, then open /ui/ (no second port or frontend process)

worker: ## Run the multi-queue background worker (ingest + embed + summarize + demo queues)
	$(UV) run ref-showcase-worker

migrate: ## Apply DB migrations (also happens at app startup; this is the explicit path)
	$(UV) run python -c "from ref_showcase.resources import build_resources; build_resources(); print('migrated')"

seed: ## Generate the deterministic in-repo corpus into the DB + bucket (needs infra up)
	$(UV) run python -m ref_showcase.seed

seed-live: ## Seed via the real inference gateway (requires URL + chat/embed models)
	$(UV) run python -m ref_showcase.seed --live

test: ## Run the unit test suite (no services required)
	$(UV) run pytest

lint: ## Lint + format-check (shared ruff config) — scorecard #4
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Auto-fix lint + format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

check: ## Full gate: lint + typecheck + tests, exits non-zero on failure (scorecard #3)
	$(UV) run ruff check .
	$(UV) run pyright src
	$(UV) run pytest -q

check-live: ## Full gate against an EPHEMERAL throwaway Postgres (needs docker)
	./scripts/check-live.sh
