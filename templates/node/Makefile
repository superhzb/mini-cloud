# {{name}} (node) — standard mini-cloud task entrypoints.
.PHONY: help setup run start test check lint

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  %-8s %s\n", $$1, $$2}'

setup: ## Install deps + seed .env
	npm install
	@test -f .env || cp .env.example .env

run: ## Run the server on the canonical PORT
	npm start

start: run ## Alias for run (brbot-router command)

test: ## Run tests
	npm test

lint: ## Syntax check
	npm run lint

check: ## Full gate: lint + test
	npm run lint
	npm test
