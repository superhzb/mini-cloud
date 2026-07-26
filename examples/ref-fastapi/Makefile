# ref-fastapi — the standard mini-cloud task entrypoints.
#
# Every mini-cloud repo exposes these SAME target names (scorecard metric #2), so a human, CI, or
# an agent drives any repo without relearning. `make setup` then `make check` should work on a
# fresh clone.

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
	$(UV) run ref-fastapi

worker: ## Run the background job worker (separate process)
	$(UV) run ref-fastapi-worker

migrate: ## Apply DB migrations (also happens at app startup; this is the explicit path)
	$(UV) run python -c "from ref_fastapi.resources import build_resources; build_resources(); print('migrated')"

seed: ## Enqueue one demo note through the running API (needs `make run` in another shell)
	@curl -fsS -X POST localhost:$${PORT:-19204}/notes -H 'content-type: application/json' \
	  -d '{"text":"mini-cloud makes starting a demo nearly one command."}' && echo

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
